# Huawei MCP Connector

An [MCP](https://modelcontextprotocol.io) server that lets AI assistants such as Claude read your
Huawei Health data: workouts, sleep, resting heart rate, HRV, running ability and personal bests.

- **Read-only.** It only queries data. It never writes.
- **Stateless and multi-user.** Every request carries the caller's own Huawei token in HTTP headers,
  and the server stores no credentials.
- **Docker only.** One `docker compose up` command starts it.

## Tools

| Tool | What it returns |
|---|---|
| `get_recent_activities` | Recent workouts with distance, heart rate, pace, VO2max and training load |
| `get_activity_detail` | Raw samples for one workout: heart rate, speed, cadence, GPS, altitude, power |
| `get_training_summary` | Min / max / avg statistics for each sensor stream of one workout |
| `get_session_metrics` | Headline metrics for one workout: duration, heart rate, speed, pace, distance |
| `get_training_period_summary` | Totals for the last N days, also broken down by activity type |
| `get_training_period_comparison` | The last N days compared with the N days before |
| `get_training_weekly_trend` | Weekly totals for the last N weeks |
| `get_training_weekly_trend_delta` | Week-over-week changes |
| `get_sleep_records` | Nightly sleep stages, score, efficiency and wake-ups |
| `get_resting_heart_rate` | Daily resting heart rate |
| `get_hrv_stats` | Nightly HRV |
| `get_athletic_performance` | Running ability, fatigue and predicted race times |
| `get_personal_bests` | Personal records |
| `health_check` | Server liveness check |

## Run

```bash
docker compose up -d --build
```

The server listens on `http://localhost:8000/mcp`.

## Connect Claude Code

1. Sign in at [health.cloud.huawei.com](https://health.cloud.huawei.com) and open DevTools, then the
   **Network** tab. Reload the page and click any request to `hihealthbase-….things.dbankcloud.com`.
   Copy its `Authorization` request header, which looks like `Bearer DgEAA…`.
2. Register the server. The token goes into your personal Claude config, not into this repo:

```bash
claude mcp add --scope local --transport http huawei http://localhost:8000/mcp -H "Authorization: Bearer <token>" -H "x-timezone: Asia/Ho_Chi_Minh"
```

3. Ask Claude, for example: *"How did I sleep this week?"*

### When the token expires

Huawei tokens last about **1 hour**. When tools report `401`, copy a fresh token and run:

```bash
claude mcp remove huawei --scope local && claude mcp add --scope local --transport http huawei http://localhost:8000/mcp -H "Authorization: Bearer <new token>" -H "x-timezone: Asia/Ho_Chi_Minh"
```

Then reconnect with `/mcp` in Claude.

## Headers

| Header | Required | Default | Notes |
|---|---|---|---|
| `Authorization` | yes | none | `Bearer <token>` from the Huawei Health web app |
| `x-timezone` | no | container `TZ` (UTC) | IANA name. Sets day boundaries for heart-rate and HRV stats |
| `x-huawei-region` | no | `dra` | Where your data is stored. Match the host in DevTools: `hihealthbase-<region>…` |
| `x-client-id` | no | web app id | Only needed if Huawei changes it |

Supported regions: `dra` (Asia-Pacific), `dre` (Europe), `drcn` (China). With the wrong region, every
call succeeds but returns empty data.

## Configuration

These `docker compose` variables are optional. Set them in your shell or in a local `.env` file,
which git ignores.

| Variable | Default | Purpose |
|---|---|---|
| `BIND` | `127.0.0.1` | Host interface to publish on |
| `PORT` | `8000` | Host port |
| `HUAWEI_DEFAULT_REGION` | `dra` | Region used when `x-huawei-region` is missing |
| `DEFAULT_TZ` | `UTC` | Timezone used when `x-timezone` is missing |

## Deploying to a server

Run `BIND=0.0.0.0 docker compose up -d --build` and put HTTPS in front, for example with Caddy or
nginx. Tokens travel in request headers, so never expose the server over plain HTTP.

## License

[MIT](LICENSE)
