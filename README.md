# Lolz Bumper

Automatic thread bumping for [lolz.team](https://lolz.team/) with Telegram notifications.

## What it does

- Bumps specified threads on a schedule
- Uses the timer reported by the forum itself — sleeps exactly as long as needed
- Bypasses the JS challenge (`__x`) via AES decryption
- Mimics Firefox at the TLS level (via `curl_cffi`)
- Sends Telegram notifications: success, errors, expired cookies
- Responds to `/status`, `/recheck`, and `/help` commands
- On expired cookies: stops, sends a notification with a **🔄 Recheck** button — no automatic restart loop

## Requirements

- Docker + Docker Compose
- A lolz.team account
- A Telegram bot (create one via [@BotFather](https://t.me/BotFather))
- Your Telegram ID (get it from [@userinfobot](https://t.me/userinfobot))

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/sameaslooks/lolz-bumper.git
cd lolz-bumper
```

### 2. Create `.env`

Copy `.env.example` to `.env` and fill it in:

```env
# === COOKIES ===
XF_USER=
XF_TFA_TRUST=
XF_CSRF=

# === TELEGRAM ===
TG_TOKEN=
TG_OWNER_ID=
LOGS=1

# === SETTINGS ===
LANG=en
TIMEOUT=64800
JITTER_SECONDS=1800
THREAD_TIMEOUT=60
THREAD_URLS=https://lolz.team/threads/50
```

### How to get cookies

1. Log in to lolz.team in your browser.
2. Open DevTools → Application → Cookies → `https://lolz.team`.
3. Copy the values:
   - `xf_user` → `XF_USER` (**must be URL-encoded**: the comma between the user ID and the key must be `%2C`, otherwise XenForo deletes the cookie)
   - `xf_tfa_trust_XXXXXXX` → `XF_TFA_TRUST` (value only, without the prefix)
   - `xf_csrf` → `XF_CSRF` (optional but recommendeded)

> **Important:** `xf_tfa_trust` is a cookie whose name includes your user ID, e.g. `xf_tfa_trust_9350116`. In `.env` put only its value. The cookie name is hardcoded in `main.py` as `xf_tfa_trust_9350116` — if your ID differs, update that line.

> **Note:** `xf_session` is managed automatically — the server issues it via `Set-Cookie` on the first request. Do not set it in `.env`.

### 3. Run

```bash
docker compose up -d --build
```

### 4. Check logs

```bash
docker compose logs -f
```

## `.env` settings

| Variable | Description | Default | Example |
|----------|-------------|---------|---------|
| `XF_USER` | `xf_user` cookie (URL-encoded) | — | `9350116%2Cxxxxx...` |
| `XF_TFA_TRUST` | `xf_tfa_trust_XXXX` cookie value | — | `M81TzUsQDhmmE8V...` |
| `XF_CSRF` | `xf_csrf` cookie (optional) | `""` | `MSLlOY9Yyt1Z5r5T` |
| `TG_TOKEN` | Telegram bot token | — | `123456:ABC-DEF...` |
| `TG_OWNER_ID` | Your Telegram user ID | — | `123456789` |
| `LANG` | Bot language: `en` or `ru` | `en` | `ru` |
| `LOGS` | Log level: `0` — none, `1` — successes + errors, `2` — everything | `0` | `1` |
| `TIMEOUT` | Base interval between bump cycles (seconds) | — | `64800` (18 h) |
| `JITTER_SECONDS` | Maximum random delay added on top of `TIMEOUT` (seconds) | `1800` | `1800` (30 min) |
| `THREAD_TIMEOUT` | Pause between bumping individual threads (seconds) | `60` | `60` |
| `THREAD_URLS` | Comma-separated thread URLs to bump | — | `https://lolz.team/threads/50/` |

## Bot commands

| Command | Description |
|---------|-------------|
| `/status` | Current state: threads, last bump time, next cycle |
| `/recheck` | Resume from stopped state and recheck cookies |
| `/help` | List of commands |

When cookies expire, the bot also shows a **🔄 Recheck** keyboard button — tapping it is equivalent to `/recheck`.

## Updating

### Change `.env` or `main.py`

```bash
docker compose restart
```

### Rebuild with updated dependencies

```bash
docker compose up -d --build
```

## What to do if cookies expire

The bot will stop bumping and send:

```
⚠️ Cookies expired or site unavailable!
```

with a **🔄 Recheck** button and instructions. Options:

**If the site is temporarily down** — wait and tap **🔄 Recheck** (or send `/recheck`) once it is back up. No restart needed.

**If cookies are actually expired:**
1. Update `XF_USER`, `XF_TFA_TRUST`, and optionally `XF_CSRF` in `.env`.
2. Restart the container:
   ```bash
   docker compose restart
   ```

## Project structure

```
lolz-bumper/
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── main.py
├── .env
└── README.md
```

## License

[GNU GPL v3](LICENSE)
