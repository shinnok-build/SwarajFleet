# Architecture & Design — SwarajFleet

Design document for the per-robot coordination protocol. Companion to [BENCHMARK-REPORT.md](BENCHMARK-REPORT.md) (the measured evidence).

## 1. What the problem statement requires

- 3+ AMRs sharing **position + intended path peer-to-peer**, with **no central coordinator**
- Handle **conflict and deadlock** at narrow aisles, choke points, and **blocked intersections**
- **Task allocation & re-routing** (PS requirement 3); **battery status** on a dashboard; simulation accepted as the testbed

## 2. Design principles

1. **The coordinator is the bottleneck — remove it.** Evidence: stop-and-wait with *perfect information* deadlocks 94% of dense runs (our measurement); centralized planning falls 68%→35% between 8 and 10 robots on real robots (arXiv:2501.17661).
2. **One round is the minimum possible.** Auctions need bid rounds; token-passing circulates a global token. We do exactly one broadcast per robot per tick — the information-theoretic minimum to share state.
3. **Task state rides in the same broadcast.** Allocation therefore costs zero extra messages — this is the core novelty: motion *and* task decided from one single-round snapshot.
4. **Liveness by construction**, not by testing: the ordering argument in §4 holds for every tick by design; the 0/90 measurement confirms it.
5. **Zero exotic dependencies.** Pure Python, runs on a Raspberry Pi 5 / Jetson Nano; the full protocol tick is ~0.5 ms on a commodity CPU.

## 3. Per-tick workflow (what every robot runs)

```
BROADCAST   {pos, 2-step intent, task state, priority} — one message/tick (ROS 2 DDS on the private LAN)
    │
TASK GATE   claims + releases from the SAME snapshot
    │       (stuck pickup ≥20 ticks → released, nearest peer reclaims; zero extra messages)
    ▼
PERCEIVE    local snapshot: neighbors' pos + 2-step intent (comm radius; no global state)
    ▼
PLAN        congestion-aware A* — cell cost = base + λ·(observed intent heat)
    ▼
CONFLICT?   planned cell vs neighbors' 2-step intent (same snapshot)
    │
    ├── NO ─────────────────────────────► APPLY
    │
    └── YES ► YIELD to a proven-free pocket cell
              RIGHT-OF-WAY: unique order watchdog > carry > rotating (aid − epoch)
              RE-PLAN around the block — stall >30 ticks → watchdog grants temp top priority
              ───────────────────────────► APPLY
                                              │
APPLY     commit the move; pick/drop holds on arrival; never enter an occupied cell
    ▼
DASHBOARD   subscribes via WebSocket — observe-only (kill-tested)
    ▼
next tick — every robot repeats from the same broadcast
```

### The five invariants

1. **Snapshot-pure**: every decision is a pure function of the last good broadcast. No hidden state, no server, no ordering of robots — all robots act simultaneously.
2. **Conservative conflicts**: a 2-step time window (vertex + edge-swap) checked against neighbors' *intended* cells, not just current cells — conflicts are detected before they happen.
3. **Yield proves a free cell**: a yield always steps aside to a cell verified free in the snapshot — yielding never creates a new conflict.
4. **Safe by default on loss**: a robot not heard from is treated as stationary (last-known state); the protocol never plans into an unknown, and an onboard sensor-stop backstops the blind spot. **Measured** (`stress_runs.py`): one robot's broadcast frozen 30 ticks mid-run → 0 collisions, 10/10 tasks, +13 ticks, 20 local safety stops.
5. **Observe-only supervision**: the dashboard subscribes to the same broadcast the robots read. It holds no decision power — proven by killing it mid-run.

## 4. Liveness argument (deadlock-free by construction)

- **Unique total order per contested tick.** The right-of-way rank is a tuple `(watchdog-flag, carry-flag, (aid − epoch) mod n)` — the last component is unique per robot, so two robots can never tie. (Verified in code: `proto/swaraj.py`, `run_swaraj_v2`.)
- **Every yield proves a free cell** (invariant 3) — a yielding robot always has somewhere to go.
- **Watchdog bound.** Any robot stalled ≥30 ticks receives one-shot temporary top priority (`temp_top`), which breaks any residual cycle; an anti-oscillation break prevents re-entry into the same cycle.
- **Measured**: 0/90 dense-run deadlocks at 10 robots; 2/90 at 5 robots (corner class, §6).

## 5. What we tried and rejected (A/B test)

The tempting "smarter" right-of-way: **goal-distance priority** — within the non-carry tier, the robot closest to its goal keeps the path (like PIBT's utility). Implemented as `SWARAJ_RO=dist` in `proto/swaraj.py` and run on the **same 180 seeds**:

| Scheme | @5 robots | @10 robots |
|---|---|---|
| **Shipping: rotating** | 88/90 · 0 collisions · 2 deadlocks · h2h 21.6/22.5/38.8% | **90/90** · 0 collisions · **0 deadlocks** |
| Goal-distance | 88/90 · 0 collisions · 2 deadlocks · h2h 21.6/**15.0**/38.8% | **88/90** · 0 collisions · **2 new deadlocks** |

**Why it lost:** goal-distance order is *stable*, and symmetric cycles need *instability* to break: two robots circling a corner with a stable distance ordering keep the same loser every cycle, while the rotating order hands the win around each epoch and starves no one before the watchdog. **Decision: rejected; rotation ships.** The variant stays in the tree as the A/B harness — research depth, not clutter.

## 6. Known limits & roadmap

**Disclosed limits** (never hidden):

- 2/90 residual at 5 robots: corner cycles on pocket-less corners; watchdog-bounded; 0/90 at 10.
- Head-to-head speed figure is on matched trials (n=5 at 10 robots) — where both systems finish, we finish 1.4–3× faster; the pre-finale 20/50 runs widen every n.
- Battery is tracked and broadcast (PS dashboard requirement); there is **no** recharging scheduler in the prototype — at deployment a charging dock is simply another destination in the task gate.

**Roadmap** (in priority order):

1. 20 & 50-robot stress runs (congestion-aware routing is where the advantage should grow)
2. Message delay/loss injection in the benchmark — 30-tick broadcast freeze is locked (`proto/results_stress.json`); finer-grained delay injection is pre-finale
3. Real-hardware port (Pi 5 / Jetson + ROS 2 DDS; the protocol module is the only code that moves)
4. Pocket-placement rule generalization (target: eliminate the 5-robot corner class)

## 7. Hardware & stack (what the robots run)

| Function | Choice |
|---|---|
| Edge compute | Raspberry Pi 5 / Jetson Nano per AMR |
| Protocol | Python (this repo) — ~400-line protocol module |
| Comms | ROS 2 Humble, DDS pub/sub, private warehouse LAN — no internet, no cloud |
| Planning | Congestion-aware A* + right-of-way + watchdog (all on-board) |
| Dashboard | WebSocket + HTML/JS, observe-only |
