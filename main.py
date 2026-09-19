import os
import random
import re
import threading
import time
from datetime import datetime, timedelta, timezone

import requests
from Crypto.Cipher import AES  # type: ignore
from curl_cffi import requests as curl_requests  # type: ignore
from dotenv import load_dotenv


# =========================
# КОНФИГ ИЗ .env
# =========================

load_dotenv()


def get_config() -> dict:
    def req(key: str) -> str:
        val = os.getenv(key)
        if val is None or val == '':
            raise RuntimeError(f'Не задана переменная окружения: {key}')
        return val

    def opt(key: str, default=None):
        return os.getenv(key, default)

    thread_urls_raw = req('THREAD_URLS')
    thread_urls = [u.strip() for u in thread_urls_raw.split(',') if u.strip()]

    owner_id_raw = req('TG_OWNER_ID')
    try:
        owner_id = int(owner_id_raw)
    except ValueError:
        raise RuntimeError(f'TG_OWNER_ID должен быть числом, получено: {owner_id_raw!r}')

    if owner_id <= 0:
        raise RuntimeError(f'TG_OWNER_ID должен быть положительным, получено: {owner_id}')

    token = req('TG_TOKEN')

    return {
        'cookies': {
            'xf_user': req('XF_USER'),
            'xf_tfa_trust': req('XF_TFA_TRUST'),
            'xf_session': opt('XF_SESSION', ''),
            'xf_csrf': opt('XF_CSRF', ''),
        },
        'bot_settings': {
            'token': token,
            'owner_id': owner_id,
            'logs': int(opt('LOGS', '0')),
        },
        'settings': {
            'timeout': int(req('TIMEOUT')),
            'jitter_seconds': int(opt('JITTER_SECONDS', '1800')),
            'thread_timeout': int(opt('THREAD_TIMEOUT', '60')),
            'thread_urls': thread_urls,
        },
    }


# =========================
# ГЛОБАЛЬНОЕ СОСТОЯНИЕ
# =========================

STATE = {
    'threads': {},          # thread_id -> {'title': str, 'url': str}
    'last_bump': None,      # datetime UTC
    'next_cycle': None,     # datetime UTC
    'cookies_ok': True,
    'started_at': None,
    'stopped': False,       # True = цикл остановлен (куки протухли)
}

STATE_LOCK = threading.Lock()


def wait_forever():
    """Бесконечный сон — не даёт процессу завершиться, контейнер остаётся жив."""
    while True:
        time.sleep(3600)


# =========================
# AES ЧЕЛЛЕНДЖ (__x)
# =========================

MAIN_PATTERN = re.compile(
    r'main\(\s*"([0-9a-f]{32})"\s*,\s*"([0-9a-f]{32})"\s*,\s*"([0-9a-f]{32})"\s*\)'
)


def extract_main_args(html: str):
    match = MAIN_PATTERN.search(html)
    if not match:
        return None
    return match.group(1), match.group(2), match.group(3)


def decrypt_x(data_hex: str, key_hex: str, iv_hex: str) -> str:
    cipher = AES.new(
        bytearray.fromhex(key_hex),
        AES.MODE_CBC,
        bytearray.fromhex(iv_hex),
    )
    return cipher.decrypt(bytearray.fromhex(data_hex)).hex()


def is_challenge(html: str) -> bool:
    return 'main(' in html and '_dfjs' in html


def refresh_x(session: curl_requests.Session, config: dict) -> bool:
    try:
        r = session.get('https://lolz.team/', impersonate='firefox', timeout=30)
    except Exception as e:
        log(config, f'[!] Не удалось загрузить главную: {e}', level=2)
        return False

    args = extract_main_args(r.text)
    if not args:
        log(config, '[!] Не найдены аргументы main(...) в HTML', level=2)
        return False

    data_hex, key_hex, iv_hex = args
    try:
        x_value = decrypt_x(data_hex, key_hex, iv_hex)
    except Exception as e:
        log(config, f'[!] Ошибка расшифровки __x: {e}', level=2)
        return False

    session.cookies.set('__x', x_value, domain='lolz.team')
    log(config, f'[*] __x обновлён: {x_value[:16]}...', level=2)
    return True


# =========================
# _xfToken
# =========================

XF_TOKEN_PATTERN = re.compile(r'name="_xfToken"\s+value="([^"]+)"')


def get_xf_token(session: curl_requests.Session, config: dict) -> str | None:
    try:
        r = session.get(
            'https://lolz.team/?tab=mythreads',
            impersonate='firefox',
            timeout=30,
        )
    except Exception as e:
        log(config, f'[!] Не удалось получить _xfToken: {e}', level=2)
        return None

    match = XF_TOKEN_PATTERN.search(r.text)
    if not match:
        log(config, '[!] _xfToken не найден в HTML', level=2)
        return None
    return match.group(1)


# =========================
# НАЗВАНИЕ ТЕМЫ
# =========================

H1_TITLE_PATTERN = re.compile(
    r'titleThreadGroup.*?<h1[^>]*\btitle="([^"]+)"',
    re.DOTALL | re.IGNORECASE,
)
H1_TITLE_FALLBACK = re.compile(
    r'<h1[^>]*\btitle="([^"]+)"',
    re.IGNORECASE,
)
TITLE_TAG_PATTERN = re.compile(
    r'<title>(.*?)</title>',
    re.DOTALL | re.IGNORECASE,
)


def _clean_title(raw: str) -> str:
    raw = re.sub(r'<[^>]+>', '', raw)
    raw = re.sub(r'\s+', ' ', raw).strip()
    raw = re.sub(r'\s*[|—\-]\s*Lolz.*$', '', raw, flags=re.IGNORECASE).strip()
    return raw


def fetch_thread_title(session: curl_requests.Session, config: dict,
                       thread_id: str) -> str:
    url = f'https://lolz.team/threads/{thread_id}/'
    try:
        r = session.get(url, impersonate='firefox', timeout=30)
    except Exception:
        return f'Тема {thread_id}'

    if is_challenge(r.text):
        refresh_x(session, config)
        try:
            r = session.get(url, impersonate='firefox', timeout=30)
        except Exception:
            return f'Тема {thread_id}'

    match = H1_TITLE_PATTERN.search(r.text)
    if match:
        title = _clean_title(match.group(1))
        if title:
            return title

    match = H1_TITLE_FALLBACK.search(r.text)
    if match:
        title = _clean_title(match.group(1))
        if title:
            return title

    match = TITLE_TAG_PATTERN.search(r.text)
    if match:
        title = _clean_title(match.group(1))
        if title:
            return title

    return f'Тема {thread_id}'


# =========================
# ПАРСИНГ ТАЙМЕРА ИЗ ОШИБКИ
# =========================

WAIT_PATTERN = re.compile(
    r'подождать\s+'
    r'(?:(\d+)\s+час(?:ов|а|)?)?\s*'
    r'(?:(\d+)\s+минут(?:ы|у|)?)?\s*'
    r'(?:(\d+)\s+секунд(?:ы|у|)?)?',
    re.IGNORECASE,
)


def parse_wait_seconds(text: str) -> int | None:
    match = WAIT_PATTERN.search(text)
    if not match:
        return None
    h = int(match.group(1) or 0)
    m = int(match.group(2) or 0)
    s = int(match.group(3) or 0)
    total = h * 3600 + m * 60 + s
    return total if total > 0 else None


# =========================
# НОРМАЛИЗАЦИЯ ОШИБОК
# =========================

SPLIT_LETTERS_PATTERN = re.compile(r'^(?:\S;\s*)+\S?$')


def normalize_error(msg: str) -> str:
    stripped = msg.strip()
    if not stripped:
        return msg

    if SPLIT_LETTERS_PATTERN.match(stripped):
        result = stripped.replace('; ', '').replace(';', '')
        return result.strip()

    return msg


# =========================
# TELEGRAM
# =========================

def tg_api(config: dict, method: str, payload: dict) -> dict | None:
    token = config['bot_settings'].get('token')
    if not token:
        return None
    try:
        r = requests.post(
            f'https://api.telegram.org/bot{token}/{method}',
            json=payload,
            timeout=30,
        )
        if r.status_code != 200:
            print(f'[!] Telegram API error ({method}): {r.status_code} {r.text[:300]}', flush=True)
        return r.json()
    except Exception as e:
        print(f'[!] Telegram API error ({method}): {e}', flush=True)
        return None


def is_owner(config: dict, chat_id: int) -> bool:
    """Проверка, что chat_id — это владелец бота."""
    owner_id = config['bot_settings'].get('owner_id')
    return bool(owner_id) and chat_id == owner_id


def send_telegram(config: dict, text: str):
    """Отправка владельцу (только ему)."""
    owner_id = config['bot_settings'].get('owner_id')
    if not owner_id:
        return

    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)

    tg_api(config, 'sendMessage', {
        'chat_id': owner_id,
        'text': text,
        'parse_mode': 'HTML',
        'disable_web_page_preview': True,
    })


def send_telegram_to(config: dict, chat_id: int, text: str):
    """Отправка в конкретный чат — только если это владелец."""
    if not is_owner(config, chat_id):
        print(f'[!] send_telegram_to: chat_id={chat_id} не владелец, игнорирую', flush=True)
        return

    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    tg_api(config, 'sendMessage', {
        'chat_id': chat_id,
        'text': text,
        'parse_mode': 'HTML',
        'disable_web_page_preview': True,
    })


def log(config: dict, text: str, level: int = 2):
    """
    level: 0 — ничего
           1 — успех + ошибки bump (без шума)
           2 — всё (успех + ошибки + диагностика)
    """
    console_text = re.sub(r'</?(?:b|i|code|u|s|pre)>', '', text)
    console_text = re.sub(r'</?a[^>]*>', '', console_text)
    console_text = re.sub(r'<br\s*/?>', '\n', console_text, flags=re.IGNORECASE)
    print(console_text, flush=True)

    logs = config['bot_settings'].get('logs', 0)
    if logs >= level:
        send_telegram(config, text)


# =========================
# BUMP
# =========================

AUTH_ERROR_MARKERS = (
    'авторизация',
    'session',
    'login',
    'log in',
    'logged in',
    'must be logged',
    'войдите',
    'недействительн',
)


def bump_thread(session: curl_requests.Session, config: dict,
                thread_id: str, xf_token: str) -> tuple[bool, str]:
    url = (
        f'https://lolz.team/threads/{thread_id}/bump'
        f'?_xfRequestUri=%2Fthreads%2F{thread_id}%2F'
        f'&_xfNoRedirect=1'
        f'&_xfToken={xf_token}'
        f'&_xfResponseType=json'
    )
    try:
        r = session.get(
            url,
            impersonate='firefox',
            headers={
                'Referer': f'https://lolz.team/threads/{thread_id}/',
                'X-Requested-With': 'XMLHttpRequest',
                'Accept': 'application/json, text/javascript, */*; q=0.01',
            },
            timeout=30,
        )
    except Exception as e:
        return False, f'network error: {e}'

    try:
        data = r.json()
    except Exception:
        return False, f'non-json response ({r.status_code})'

    if data.get('status') == 'ok':
        return True, 'ok'

    errors = data.get('error')
    if errors:
        if isinstance(errors, list):
            raw = '; '.join(str(e) for e in errors)
        else:
            raw = str(errors)
        return False, normalize_error(raw)

    return False, f'unknown response: {data}'


# =========================
# ПАРСИНГ ID ТЕМЫ
# =========================

THREAD_ID_PATTERN = re.compile(r'/threads/(\d+)')


def extract_thread_ids(urls: list) -> list:
    ids = []
    for u in urls:
        m = THREAD_ID_PATTERN.search(u)
        if m:
            ids.append(m.group(1))
    return ids


# =========================
# ИНТЕРВАЛЫ
# =========================

def jittered(seconds: int, jitter_seconds: int) -> float:
    return seconds + random.uniform(0, jitter_seconds)


# =========================
# ФОРМАТИРОВАНИЕ
# =========================

def fmt_dt(dt: datetime | None) -> str:
    if dt is None:
        return '—'
    return dt.strftime('%Y-%m-%d %H:%M:%S UTC')


def fmt_duration(seconds: float) -> str:
    if seconds >= 3600:
        return f'{seconds/3600:.2f} ч'
    if seconds >= 60:
        return f'{seconds/60:.1f} мин'
    return f'{int(seconds)} сек'


def thread_label(thread_id: str) -> str:
    with STATE_LOCK:
        info = STATE['threads'].get(thread_id)
    if not info:
        return f'Тема {thread_id}'
    return info['title']


def thread_link(thread_id: str) -> str:
    with STATE_LOCK:
        info = STATE['threads'].get(thread_id)
    if not info:
        return f'https://lolz.team/threads/{thread_id}/'
    return info['url']


# =========================
# КОМАНДЫ БОТА
# =========================

HELP_TEXT = (
    '<b>🤖 Lolz Bumper — команды</b>\n\n'
    '<b>/status</b> — текущее состояние: темы, последний bump, следующий цикл\n'
    '<b>/help</b> — это сообщение\n'
)


def handle_status(config: dict, chat_id: int):
    if not is_owner(config, chat_id):
        print(f'[!] handle_status: chat_id={chat_id} не владелец', flush=True)
        return

    with STATE_LOCK:
        threads = dict(STATE['threads'])
        last_bump = STATE['last_bump']
        next_cycle = STATE['next_cycle']
        cookies_ok = STATE['cookies_ok']
        started_at = STATE['started_at']
        stopped = STATE['stopped']

    lines = ['<b>📊 Статус Lolz Bumper</b>\n']

    if threads:
        lines.append('<b>Темы:</b>')
        for tid, info in threads.items():
            lines.append(f'• <a href="{info["url"]}">{info["title"]}</a>')
    else:
        lines.append('<b>Темы:</b> не загружены')

    lines.append('')

    if stopped:
        lines.append('<b>Состояние:</b> ⛔ остановлен (куки протухли)')
        lines.append('<i>Обнови .env и сделай docker compose restart</i>')
    else:
        lines.append(f'<b>Куки:</b> {"✅ валидны" if cookies_ok else "❌ протухли"}')
        lines.append(f'<b>Последний bump:</b> {fmt_dt(last_bump)}')
        lines.append(f'<b>Следующий цикл:</b> {fmt_dt(next_cycle)}')

        if next_cycle:
            now = datetime.now(timezone.utc)
            left = (next_cycle - now).total_seconds()
            if left > 0:
                lines.append(f'<b>Осталось:</b> {fmt_duration(left)}')

    lines.append(f'\n<b>Запущен:</b> {fmt_dt(started_at)}')

    send_telegram_to(config, chat_id, '\n'.join(lines))


def handle_help(config: dict, chat_id: int):
    if not is_owner(config, chat_id):
        print(f'[!] handle_help: chat_id={chat_id} не владелец', flush=True)
        return
    send_telegram_to(config, chat_id, HELP_TEXT)


# =========================
# TELEGRAM POLLING
# =========================

def telegram_polling(config: dict):
    token = config['bot_settings'].get('token')
    owner_id = config['bot_settings'].get('owner_id')
    if not token or not owner_id:
        print('[!] Polling отключён: нет token или owner_id', flush=True)
        return

    offset = None
    try:
        r = requests.get(
            f'https://api.telegram.org/bot{token}/getUpdates',
            params={'offset': -1, 'timeout': 0},
            timeout=15,
        )
        data = r.json()
        if data.get('ok') and data.get('result'):
            offset = data['result'][-1]['update_id'] + 1
            print(f'[*] Polling: пропускаю старые апдейты, offset={offset}', flush=True)
    except Exception as e:
        print(f'[!] Polling init error: {e}', flush=True)

    while True:
        try:
            params = {'timeout': 30}
            if offset is not None:
                params['offset'] = offset

            r = requests.get(
                f'https://api.telegram.org/bot{token}/getUpdates',
                params=params,
                timeout=40,
            )
            data = r.json()
            if not data.get('ok'):
                time.sleep(5)
                continue

            for update in data.get('result', []):
                offset = update['update_id'] + 1
                msg = update.get('message')
                if not msg:
                    continue

                chat_id = msg['chat']['id']

                # 🔒 ГЛАВНАЯ ПРОВЕРКА: игнорируем всех, кроме владельца
                if chat_id != owner_id:
                    print(f'[!] Игнорирую сообщение от chat_id={chat_id} '
                          f'(username={msg["chat"].get("username")})', flush=True)
                    continue

                text = (msg.get('text') or '').strip()

                if text in ('/start', '/help'):
                    threading.Thread(
                        target=handle_help,
                        args=(config, chat_id),
                        daemon=True,
                    ).start()
                elif text == '/status':
                    threading.Thread(
                        target=handle_status,
                        args=(config, chat_id),
                        daemon=True,
                    ).start()
        except Exception as e:
            print(f'[!] Polling error: {e}', flush=True)
            time.sleep(5)


# =========================
# ПРОВЕРКА КУК
# =========================

def check_cookies(session: curl_requests.Session) -> bool:
    try:
        r = session.get(
            'https://lolz.team/?tab=mythreads',
            impersonate='firefox',
            timeout=30,
        )
    except Exception:
        return False
    return bool(XF_TOKEN_PATTERN.search(r.text))


# =========================
# MAIN
# =========================

def main():
    config = get_config()

    base_interval = config['settings']['timeout']
    jitter_seconds = config['settings']['jitter_seconds']
    thread_timeout = config['settings']['thread_timeout']

    thread_ids = extract_thread_ids(config['settings']['thread_urls'])
    if not thread_ids:
        log(config, '[!] Нет валидных THREAD_URLS в .env', level=2)
        wait_forever()

    log(config, f'[*] Загружено тем: {len(thread_ids)}', level=2)

    session = curl_requests.Session()
    session.cookies.set('xf_user', config['cookies']['xf_user'], domain='lolz.team')
    session.cookies.set('xf_tfa_trust_9350116', config['cookies']['xf_tfa_trust'], domain='lolz.team')
    if config['cookies'].get('xf_session'):
        session.cookies.set('xf_session', config['cookies']['xf_session'], domain='lolz.team')
    if config['cookies'].get('xf_csrf'):
        session.cookies.set('xf_csrf', config['cookies']['xf_csrf'], domain='lolz.team')

    refresh_x(session, config)

    # Polling запускаем ДО проверки кук — чтобы /status и /help работали даже при мёртвых куках
    threading.Thread(
        target=telegram_polling,
        args=(config,),
        daemon=True,
    ).start()

    if not check_cookies(session):
        with STATE_LOCK:
            STATE['cookies_ok'] = False
            STATE['stopped'] = True
            STATE['started_at'] = datetime.now(timezone.utc)
        log(config,
            '⚠️ <b>Куки невалидны при старте!</b>\n'
            'Проверь XF_USER, XF_TFA_TRUST, XF_SESSION, XF_CSRF в .env.\n'
            'Останавливаюсь. Жду перезапуска.',
            level=1)
        wait_forever()

    with STATE_LOCK:
        STATE['cookies_ok'] = True
        STATE['started_at'] = datetime.now(timezone.utc)

    # Загрузка названий тем
    for tid in thread_ids:
        title = fetch_thread_title(session, config, tid)
        with STATE_LOCK:
            STATE['threads'][tid] = {
                'title': title,
                'url': f'https://lolz.team/threads/{tid}/',
            }
        log(config, f'[*] Тема {tid}: {title}', level=2)

    while True:
        with STATE_LOCK:
            if STATE['stopped']:
                time.sleep(3600)
                continue

        xf_token = get_xf_token(session, config)
        if not xf_token:
            log(config, '[!] Не удалось получить _xfToken, пропускаю цикл', level=2)
            with STATE_LOCK:
                STATE['cookies_ok'] = False
                STATE['stopped'] = True
            log(config,
                '⚠️ <b>Куки протухли!</b>\n'
                'Обнови XF_USER, XF_TFA_TRUST, XF_SESSION, XF_CSRF в .env '
                'и сделай docker compose restart.\n'
                'Останавливаюсь. Жду перезапуска.',
                level=1)
            wait_forever()

        with STATE_LOCK:
            STATE['cookies_ok'] = True

        random.shuffle(thread_ids)

        wait_seconds = None

        for thread_id in thread_ids:
            ok, msg = bump_thread(session, config, thread_id, xf_token)
            title = thread_label(thread_id)
            link = thread_link(thread_id)

            if ok:
                now = datetime.now(timezone.utc)
                with STATE_LOCK:
                    STATE['last_bump'] = now
                log(
                    config,
                    f'<b>✅ Тема поднята</b>\n\n'
                    f'📌 {title}\n'
                    f'🔗 {link}\n'
                    f'🕐 {fmt_dt(now)}',
                    level=1,
                )
            else:
                short_msg = msg.replace('\n', ' ').strip()
                log(
                    config,
                    f'<b>⚠️ Не удалось поднять</b>\n\n'
                    f'📌 {title}\n'
                    f'🔗 {link}\n'
                    f'📝 {short_msg}',
                    level=1,
                )

                w = parse_wait_seconds(msg)
                if w is not None:
                    if wait_seconds is None or w < wait_seconds:
                        wait_seconds = w

                lower = msg.lower()
                if 'нарушение безопасности' in lower:
                    log(config, '[*] Похоже, __x протух — обновляю', level=2)
                    refresh_x(session, config)
                elif any(marker in lower for marker in AUTH_ERROR_MARKERS):
                    with STATE_LOCK:
                        STATE['cookies_ok'] = False
                        STATE['stopped'] = True
                    log(config,
                        '⚠️ <b>Куки протухли!</b>\n'
                        'Обнови XF_USER, XF_TFA_TRUST, XF_SESSION, XF_CSRF в .env '
                        'и сделай docker compose restart.\n'
                        'Останавливаюсь. Жду перезапуска.',
                        level=1)
                    wait_forever()

            if len(thread_ids) > 1:
                time.sleep(jittered(thread_timeout, 15))

        if wait_seconds is not None:
            extra = random.uniform(30, max(30, jitter_seconds))
            sleep_for = wait_seconds + extra
            source = f'форум сказал ждать {fmt_duration(wait_seconds)} (+{int(extra)} сек)'
        else:
            sleep_for = jittered(base_interval, jitter_seconds)
            source = 'TIMEOUT + jitter'

        next_cycle = datetime.now(timezone.utc) + timedelta(seconds=sleep_for)
        with STATE_LOCK:
            STATE['next_cycle'] = next_cycle

        log(
            config,
            f'[*] Сплю {fmt_duration(sleep_for)} до следующего цикла ({source})',
            level=2,
        )
        time.sleep(sleep_for)


if __name__ == '__main__':
    main()