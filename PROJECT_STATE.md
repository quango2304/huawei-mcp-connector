# Project State

_Last updated: 2026-10-02_

## Status

v0.2.0 — local, single-user connector with self-refreshing login. Works.

Verified: credential login (account + password + one-time SMS + trust-device), fully silent token
refresh afterwards (re-login with no code), and real data fetch (sleep, resting HR) through the
refreshed token. Verified on the host; the container runs and reaches the SMS step. The in-container
*first* SMS login was not completed end-to-end only because Huawei was rate-limiting codes during
testing — the identical flow succeeds on the host. Workout tools are exercised on synthetic data
(the test account has no workouts).

## Architecture

```
src/huawei_mcp/
  auth.py      LoginManager: headless Playwright login + trusted profile; hands out a fresh token.
               All browser work runs in one dedicated thread (Playwright sync API is single-threaded;
               FastMCP dispatches tools across a pool). Silent refresh = re-login with password only.
  server.py    FastMCP tools (HTTP on :8000/mcp, stateless). Auth tools + 14 data tools. Each data
               tool gets a token from LoginManager, builds a client, and retries once on 401.
  queries.py   One function per tool: validate input, call client, parse, aggregate.
  client.py    httpx wrapper for the Huawei API; raw JSON; region -> host map.
  parsers.py   Raw JSON -> models; never raises on malformed data.
  analysis.py  Pure aggregation: per-workout stats, period totals, comparisons, weekly trends.
  models.py    Pydantic models returned by the tools.
```

- Runs only through `docker compose` (Playwright base image, uv inside). No tests or scripts.
- Credentials come from the environment (`HUAWEI_ACCOUNT`, `HUAWEI_PASSWORD`), set via `.env`.
- The trusted browser profile persists in the `huawei-profile` Docker volume.

## Login flow (verified against live Huawei)

- Entry: `health.cloud.huawei.com/TrainingCamp` -> click Login -> Huawei ID page on `id5.cloud.huawei.com`.
- Fill phone + password -> click **LOG IN** (match the button by exact text; "Log in via SMS" heading
  also contains "log in").
- Untrusted browser -> **Verify identity** modal (auto-sends SMS); enter code -> click **OK**.
- **Trust this browser?** dialog -> click **TRUST** (this is what enables future code-free logins).
- The SPA exchanges the auth code and stores `accessToken` + `expireTime` in `sessionStorage`
  (token ~176 chars, ~60-min life). `site` there maps to region: 1=drcn, 5=dra, 7=dre.
- The access token alone (plus `x-client-id`) authenticates the data API; no browser needed per call.

## API facts (verified against live responses)

- Base URL: `https://hihealthbase-<region>.things.dbankcloud.{com|cn}/healthrunninggroup/v1`.
  `dra`/`dre` use `.com`, `drcn` uses `.cn`. A token works in any region but only the home region
  returns data.
- Timestamp units: activity list/detail top-level ms; detail collectors/samples ns; sleep records ns;
  sleep `go_bed/fall_asleep/wakeup` values ms; personal-best times ms.
- `periodStatistics` needs `strategy`, `groupOption="day"`, `timeZone="+HHMM"` or it 400s.
  `healthRecords` stats takes days as `"YYYYMMDD"` strings; `sampleSet` takes integers.
- The detail endpoint can't look up by id; the server finds the workout in the list first.
- `activeTime` is ms. Period pace = sum(active s) / sum(km), never a mean of paces.

## Known gaps / risks

- SMS is rate-limited after several requests in a short window (temporary).
- Captcha can appear during login (risk-based); unattended login can't solve it. Trusted device
  makes it rare. Falls back to a manual browser login.
- Regions `dre` and `drcn` not verified with real data.
- `get_personal_bests` verified only for `activity_type="running"`.

## Next

- Complete the in-container first SMS login once throttling clears (one code), confirm silent refresh
  in the container, then confirm the workout tools once the account has workouts.
- Deploy to a VPS behind HTTPS.
