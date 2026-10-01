# Project State

_Last updated: 2026-10-02_

## Status

v0.1.0 works. All 14 tools have been verified over HTTP against a live Huawei account (Asia-Pacific
region). The workout tools have only been checked on synthetic data, because the test account has no
workouts yet.

## Architecture

```
src/huawei_mcp/
  server.py    FastMCP tools; reads credentials from request headers; HTTP on :8000/mcp (stateless)
  queries.py   One function per tool: validates input, calls the client, parses, aggregates
  client.py    httpx wrapper for the Huawei API; returns raw JSON; region -> host map
  parsers.py   Raw JSON -> models; never raises on malformed data
  analysis.py  Pure aggregation: per-workout stats, period totals, comparisons, weekly trends
  models.py    Pydantic models returned by the tools
```

- Runs only through `docker compose`, using uv inside the image. There are no tests or scripts.
- No credentials are stored. Each request sends `Authorization` (required), plus optional
  `x-huawei-region`, `x-timezone` and `x-client-id` headers.

## API facts (verified against live responses)

- Base URL: `https://hihealthbase-<region>.things.dbankcloud.{com|cn}/healthrunninggroup/v1`.
  `dra` and `dre` use `.com`; `drcn` uses `.cn`. A token works in every region, but only the
  account's home region returns data.
- The token is a Huawei OAuth access token that expires after about 60 minutes. The client id
  `106533743` belongs to the web app and is the same for everyone.
- Timestamp units:
  - activity list and detail top-level times: ms
  - detail collectors and samples: ns
  - sleep records: ns
  - sleep `go_bed/fall_asleep/wakeup` values: ms
  - personal best times: ms
- `periodStatistics` requests need `strategy`, `groupOption="day"` and `timeZone="+HHMM"`,
  otherwise they return 400. The `healthRecords` stats endpoint takes days as `"YYYYMMDD"`
  strings; the `sampleSet` endpoint takes them as integers.
- The detail endpoint cannot look up a workout by id. The server finds the workout in the list
  first, then queries it by its exact start, end and type.
- `activeTime` is in ms. Period pace is `sum(active seconds) / sum(km)`, never an average of paces.

## Known gaps

- `x-huawei-region` values `dre` and `drcn` have not been verified with real data.
- `get_personal_bests` has only been verified for `activity_type="running"`.
- Tokens can't be refreshed automatically. Users paste a new one about every hour.

## Next

- Deploy to a VPS behind HTTPS.
- Check workout tools once the account has workouts.
