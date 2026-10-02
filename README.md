# Huawei MCP Connector

A local [MCP](https://modelcontextprotocol.io) server that lets an AI assistant such as Claude read
your Huawei Health data: workouts, sleep, resting heart rate, HRV, running ability and personal bests.

- **Read-only.** It only queries data. It never writes.
- **Logs in for you.** You give it your Huawei ID account and password once. It signs in, asks you for
  the one-time SMS code the first time, then **refreshes the token by itself** from then on — no more
  codes, no hourly copy-paste.
- **Single user, your machine.** Everything stays local. Runs only via Docker Compose.

## How login works

The server drives the real Huawei login in a headless browser it keeps inside the container:

1. First run: it enters your account + password, Huawei sends an **SMS code**, you give the code to the
   assistant, and it marks the browser as a **trusted device**.
2. After that: when the ~1-hour token expires, it logs in again silently with just the account and
   password (trusted device = no code). You only see a code again if Huawei drops the trust.

The headless browser is used *only* to obtain the token; data requests use plain HTTPS with it.

## Tools

| Tool | Purpose |
|---|---|
| `login_status` | Whether a valid session is held |
| `start_login` | Begin login; sends the SMS (or logs in silently if already trusted) |
| `submit_sms_code` | Finish the first login with the SMS code; marks the device trusted |
| `get_recent_activities` | Recent workouts (distance, HR, pace, VO2max, training load) |
| `get_activity_detail` | Raw samples for one workout (HR, speed, cadence, GPS, altitude, power) |
| `get_training_summary` | Min/max/avg per sensor stream for one workout |
| `get_session_metrics` | Headline metrics for one workout (duration, HR, speed, pace, distance) |
| `get_training_period_summary` | Totals for the last N days, also by activity type |
| `get_training_period_comparison` | Last N days vs the N before |
| `get_training_weekly_trend` | Weekly totals for the last N weeks |
| `get_training_weekly_trend_delta` | Week-over-week changes |
| `get_sleep_records` | Nightly sleep stages, score, efficiency, wake-ups |
| `get_resting_heart_rate` | Daily resting heart rate |
| `get_hrv_stats` | Nightly HRV |
| `get_athletic_performance` | Running ability, fatigue, predicted race times |
| `get_personal_bests` | Personal records |
| `health_check` | Server liveness check |

## Setup

### 1. Configure your account

Create a `.env` file next to `docker-compose.yml` (it is git-ignored):

```dotenv
HUAWEI_ACCOUNT=your_huawei_id_phone_or_email
HUAWEI_PASSWORD=your_password
HUAWEI_TZ=Asia/Ho_Chi_Minh   # your timezone, for daily heart-rate/HRV windows
BIND=127.0.0.1               # this machine only
```

### 2. Start it

```bash
docker compose up -d --build
```

First build pulls the Playwright browser image (~1.8 GB) and takes a few minutes. The server then
listens on `http://localhost:8000/mcp`.

### 3. Connect Claude Code

```bash
claude mcp add --scope local --transport http huawei http://localhost:8000/mcp
```

No token or header needed — the server authenticates itself.

### 4. First login

In Claude, ask it to **start login**. Claude calls `start_login`, Huawei texts you a code, you tell
Claude the code, and Claude calls `submit_sms_code`. Done — from now on it refreshes silently.

Then ask things like *"How did I sleep this week?"* or *"What's my 7-day running volume vs last week?"*

## Configuration

Set these in `.env`:

| Variable | Default | Purpose |
|---|---|---|
| `HUAWEI_ACCOUNT` | — (required) | Huawei ID phone or email |
| `HUAWEI_PASSWORD` | — (required) | Huawei ID password |
| `HUAWEI_TZ` | `UTC` | IANA timezone for daily resting-HR/HRV windows |
| `BIND` | `127.0.0.1` | Host interface to publish on |
| `PORT` | `8000` | Host port |

The trusted browser profile is kept in a Docker volume (`huawei-profile`), so it survives restarts and
you don't re-enter a code after `docker compose up`.

## Security

- Your Huawei **account and password** sit in `.env` on your machine and in the container's environment.
  They control your whole Huawei ID, so keep `.env` private; it is git-ignored.
- The trusted-device cookie lives in the `huawei-profile` volume. Treat it like a login session.
- The server binds to `127.0.0.1` by default. To reach it from elsewhere, deploy to a server with
  `BIND=0.0.0.0` **behind HTTPS** (e.g. Caddy or nginx) — never expose it over plain HTTP.

## Caveats

- **Rate limiting:** Huawei throttles both SMS codes and login attempts if you make many in a short
  time. If `start_login` says an SMS was sent but none arrives, or a tool reports *"Huawei is
  rate-limiting logins"*, wait a while (up to a few hours) and try again. In normal use this never
  happens — a trusted device refreshes silently without new codes.
- **Captcha:** Huawei can demand a captcha during login (risk-based; rare on a trusted device). If it
  does, `start_login` returns `captcha_required` and you'll need to log in once in a normal browser.
- **Region:** detected automatically from your account at login (Asia / Europe / China).

## License

[MIT](LICENSE)
