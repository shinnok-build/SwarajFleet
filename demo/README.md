# demo/ — live web demo

Split-screen, same-seed, live: **stop-and-wait vs SwarajFleet** (layout A, seed 8, 10 robots, 4 Hz ticks) — including the **kill-dashboard proof** (the dashboard is an observe-only subscriber; killing it mid-run never affects the fleet).

## Run

```bash
python3 server.py            # → http://localhost:8321
python3 server.py 9000       # custom port
PORT=9000 python3 server.py  # or via env
```

Stdlib only — nothing to install. The runs live in background threads and **keep running with zero viewers connected** (that is the point: the fleet does not need its dashboard).

## Files

| File | What it is |
|---|---|
| `server.py` | SSE streaming server (single long-lived channel per browser; `/control` posts for subscribe/rerun; recorded-run replay endpoint) |
| `index.html` | Vanilla-JS client — warehouse map, tick counters, battery KPIs, baseline toggle, kill-dashboard button |
| `demo_replay.json` | One recorded run, served by the replay endpoint |

## Recorded video

The 70-second recorded demo (with audio narration of the kill-dashboard moment) is in [../media/SIH26123-DEMO.mp4](../media/SIH26123-DEMO.mp4).
