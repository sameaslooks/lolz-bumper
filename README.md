# Lolz Bumper

Automatic thread bumping for [lolz.team](https://lolz.team/) with Telegram notifications.

## What it does

- Bumps specified threads on a schedule
- Uses the timer reported by the forum itself — sleeps exactly as long as needed
- Bypasses the JS challenge (`__x`) via AES decryption
- Mimics Firefox at the TLS level (via `curl_cffi`)
- Sends Telegram notifications: success, errors, expired cookies
- Responds to `/status` and `/help` commands
- On expired cookies, stops and waits for restart (no restart loop)

## Requirements

- Docker + Docker Compose
- A lolz.team account
- A Telegram bot (create one via [@BotFather](https://t.me/BotFather))
- Your Telegram ID (get it from [@userinfobot](https://t.me/userinfobot))

## Installation

### 1. Clone the repository

```bash
git clone <your-repo>
cd lolz-bumper
```

### 2. Create `.env`

Copy `.env.example` to `.env` and fill it in:

```env
# === COOKIES ===
XF_USER=
XF_TFA_TRUST=
XF_SESSION=
XF_CSRF=

# === TELEGRAM ===
TG_TOKEN=
TG_OWNER_ID=
LOGS=1

# === SETTINGS ===
TIMEOUT=64800
JITTER_SECONDS=1800
THREAD_TIMEOUT=60
THREAD_URLS=https://lolz.team/threads/10302952/
```

### How to get cookies

1. Log in to lolz.team in your browser.
2. Open DevTools → Application → Cookies → `https://lolz.team`.
3. Copy the values:
   - `xf_user` → `XF_USER`
   - `xf_tfa_trust_XXXXXXX` → `XF_TFA_TRUST` (value only, without prefix)
   - `xf_session` → `XF_SESSION`
   - `xf_csrf` → `XF_CSRF`

**Important:** `xf_tfa_trust` is a cookie with your ID in its name, e.g. `xf_tfa_trust_9350116`. In `.env` put only its value — the ID is hardcoded in `main.py` (`xf_tfa_trust_9350116`). If your ID differs, edit `main.py`.

### 3. Run

```bash
docker compose up -d --build
```

### 4. Check logs

```bash
docker compose logs -f
```

## `.env` settings

| Variable | Description | Example |
|----------|-------------|---------|
| `XF_USER` | `xf_user` cookie | `9350116,xxxxx...` |
| `XF_TFA_TRUST` | `xf_tfa_trust_XXXX` cookie (value only) | `M81TzUsQDhmmE8V...` |
| `XF_SESSION` | `xf_session` cookie | `I9pS3vc5zWjbDl0...` |
| `XF_CSRF` | `xf_csrf` cookie | `MSLlOY9Yyt1Z5r5T` |
| `TG_TOKEN` | Telegram bot token | `123456:ABC-DEF...` |
| `TG_OWNER_ID` | Your Telegram ID | `123456789` |
| `LOGS` | Log level: `0` — nothing, `1` — success + errors, `2` — everything | `1` |
| `TIMEOUT` | Base interval (sec) | `64800` (18h) |
| `JITTER_SECONDS` | Positive jitter (sec) | `1800` (30 min) |
| `THREAD_TIMEOUT` | Pause between threads (sec) | `60` |
| `THREAD_URLS` | Comma-separated thread URLs | `https://lolz.team/threads/10302952/` |

## Bot commands

- `/status` — current state: threads, last bump, next cycle
- `/help` — list of commands

## Updating

### Change `.env` or `main.py`

```bash
docker compose restart
```

### Update dependencies

```bash
docker compose up -d --build
```

## What to do if cookies expire

The bot will send:

```
⚠️ Cookies expired!
Update XF_USER, XF_TFA_TRUST, XF_SESSION, XF_CSRF in .env and run docker compose restart.
Stopping. Waiting for restart.
```

What to do:
1. Update cookies in `.env`.
2. `docker compose restart`.

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

Private project. Not for public distribution.