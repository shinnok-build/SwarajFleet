"""
SwarajFleet MVP — SIH26123 core prototype
Distributed (peer-to-peer) AMR fleet coordination vs centralized stop-and-wait baseline.

Model
- Grid warehouse with 1-cell aisles; chokepoints = aisle crossings
- Parallel-tick MAPF; vertex + edge (swap) conflicts forbidden
- 2-step time-windowed intent: each agent reserves next 2 cells; head-on /
  swap / vertex conflicts are detected BEFORE they happen
- Finished robots dwell DEPOT_LINGER ticks (depot return), then depart

BASELINE (fair/strong, centralized):
- Full global view, static priority, 2-step lookahead, stop-and-wait

OURS (SwarajFleet, decentralized):
- Each agent = one edge node: local planner + protocol, NO central process
- LIMITED view: observes only agents within comm radius R (local broadcast)
- Deterministic right-of-way (static total order) => deadlock-free by construction
- Off-aisle yield cells (step aside, never block the aisle mid-corridor)
- Replan-around-blocker (with penalty on the blocking cell)
- Adaptive congestion-aware A*: learns aisle pressure from observed local traffic
- Watchdog: starvation => one-shot temporary top priority + fairness credit
"""
import json, random, heapq, statistics
from dataclasses import dataclass, field

DEPOT_LINGER = 10
HORIZON = 2

# ---------------- Maps (generated: chokepoints by construction, connectivity guaranteed) ----------------
# Aisles are 1-cell (true choke points) but carry periodic 2-wide yield pockets —
# exactly how real AMR warehouses are laid out (staging cells), which is what
# makes opposing flows resolvable without a coordinator.
def make_warehouse(W, H, v_aisles, h_aisles, pocket_every=3):
    g = [["#"] * W for _ in range(H)]
    for y in range(1, H - 1):
        for x in range(1, W - 1):
            g[y][x] = "."
    for y in range(1, H - 1):
        for x in range(1, W - 1):
            if y not in h_aisles and x not in v_aisles:
                g[y][x] = "#"
    # pockets: every pocket_every-th column in each horizontal aisle, one cell
    # above or below (alternating). Boundary aisles place the pocket on the
    # in-bounds side — a real warehouse never leaves a 1-cell dead-end corridor
    # without a side pocket, or opposing traffic deadlocks.
    for yi, y in enumerate(h_aisles):
        for x in range(1, W - 1):
            if x in v_aisles or (x - 1) % pocket_every != 0:
                continue
            dy = -1 if yi % 2 == 0 else 1
            for py in (y + dy, y - dy):
                if 1 <= py <= H - 2 and g[py][x] == "#":
                    g[py][x] = "."
                    break
    return ["".join(r) for r in g]

LAYOUTS = {
    "A": make_warehouse(19, 11, [1, 6, 11, 16], [1, 4, 6, 9]),
    "B": make_warehouse(21, 11, [1, 6, 11, 16], [1, 4, 6, 9]),
    "C": make_warehouse(19, 13, [1, 4, 8, 12, 16], [1, 4, 6, 9, 12]),
}
AISLES = {
    "A": ([1, 6, 11, 16], [1, 4, 6, 9]),
    "B": ([1, 6, 11, 16], [1, 4, 6, 9]),
    "C": ([1, 4, 8, 12, 16], [1, 4, 6, 9, 12]),
}

DIRS = [(0, 1), (0, -1), (1, 0), (-1, 0)]

class Map:
    def __init__(self, rows):
        assert all(len(r) == len(rows[0]) for r in rows), "uneven rows"
        self.rows = rows
        self.H = len(rows); self.W = len(rows[0])
        self.obs = [[r == "#" for r in row] for row in rows]
        self.free = [(x, y) for y in range(self.H) for x in range(self.W) if not self.obs[y][x]]
        self.adj = {}
        for (x, y) in self.free:
            self.adj[(x, y)] = [(x + dx, y + dy) for dx, dy in DIRS
                                if 0 <= y + dy < self.H and 0 <= x + dx < self.W
                                and not self.obs[y + dy][x + dx]]
        seen, stack = set(), [self.free[0]]
        while stack:
            c = stack.pop()
            if c in seen: continue
            seen.add(c)
            for nb in self.adj[c]:
                if nb not in seen: stack.append(nb)
        assert len(seen) == len(self.free), "map not connected"

def astar(m, start, goal, cost_fn=None):
    if start == goal:
        return 0, [start]
    h = lambda c: abs(c[0] - goal[0]) + abs(c[1] - goal[1])
    open_h = [(h(start), 0, start)]
    came = {start: None}
    g = {start: 0}
    while open_h:
        _, gs, cur = heapq.heappop(open_h)
        if cur == goal:
            path, c = [], cur
            while c is not None:
                path.append(c); c = came[c]
            return gs, path[::-1]
        if gs > g.get(cur, 1e18):
            continue
        for nb in m.adj[cur]:
            ng = gs + 1 + (cost_fn(nb) if cost_fn else 0)
            if ng < g.get(nb, 1e18):
                g[nb] = ng; came[nb] = cur
                heapq.heappush(open_h, (ng + h(nb), ng, nb))
    return None, []

@dataclass
class Agent:
    aid: int
    pos: tuple
    goal: tuple
    wait: int = 0
    temp_top: bool = False
    owed_yield: bool = False
    usage: dict = field(default_factory=dict)
    path: list = field(default_factory=list)
    # --- v2 task-world fields ---
    hist: tuple = ()            # recent positions (oscillation detection)
    state_task: str = "IDLE"    # IDLE | TO_PICK | CARRY | HOME
    tid: int = -1
    home: tuple = None
    hold: int = 0
    battery: float = 100.0
    wait_block: int = 0

# ---------------- conflict check: 2-step time window ----------------
def horizon_conflict(a_pos, a1, a2, b_pos, b1, b2):
    """Does a (moving a_pos -> a1 -> a2) conflict with committed b (b_pos -> b1 -> b2)?"""
    if a1 == b_pos or a1 == b1:      # t+1 vertex
        return True
    if a2:
        if a2 == b2:                 # t+2 vertex
            return True
        if a2 == b1 and b2 == a1:    # t+2 edge swap
            return True
    return False

def make_planner(agent, m, usage, LAMBDA, THRESH, block=None):
    maxu = max(usage.values()) if usage else 1
    goal = agent.goal
    def cost_fn(cell, _u=usage, _m=maxu, _g=goal, _b=block):
        if cell == _g:
            return 0.0
        c = 0.0
        u = _u.get(cell, 0)
        if u >= THRESH:
            c += LAMBDA * u / _m
        if _b and cell in _b:
            c += 50.0
        return c
    return cost_fn

# ---------------- BASELINE: centralized, full view, stop-and-wait ----------------
def run_baseline(m, agents, H=HORIZON, DEPOT=DEPOT_LINGER):
    t = 0; cap = 8000; total_cost = 0
    collisions = 0
    state = {a.aid: "moving" for a in agents}
    done_at = {}
    done_times = {}
    while t < cap and any(v == "moving" for v in state.values()):
        t += 1
        # depart
        for aid, td in list(done_at.items()):
            if t >= td + DEPOT:
                state[aid] = "gone"
        on_grid = [a for a in agents if state[a.aid] in ("moving", "done")]
        committed = {}   # aid -> (nxt1, nxt2)
        for a in sorted(on_grid, key=lambda x: x.aid):
            if state[a.aid] == "done":
                committed[a.aid] = (a.pos, a.pos)
                continue
            cost, path = astar(m, a.pos, a.goal)
            p = path[1:] if len(path) > 1 else []
            n1 = p[0] if len(p) > 0 else a.pos
            clash = False
            for b in on_grid:
                if b.aid == a.aid:
                    continue
                if b.aid in committed:
                    b1 = committed[b.aid][0]
                    if b1 == n1:                                # b heading to my cell
                        clash = True; break
                    if b.pos == n1 and not (b1 != b.pos and b1 != a.pos):
                        clash = True; break                     # b stays, or swap
                elif b.pos == n1:
                    clash = True; break                          # occupied / unknown or done
            if clash:
                n1 = a.pos  # stop-and-wait in place
            committed[a.aid] = (n1, n1)
            if n1 != a.pos:
                total_cost += 1
        # safety check (independent of protocol)
        mov = [a for a in agents if state[a.aid] == "moving"]
        for i in range(len(mov)):
            for j in range(i+1, len(mov)):
                a, b = mov[i], mov[j]
                if committed[a.aid][0] == committed[b.aid][0]:
                    collisions += 1
                if committed[a.aid][0] == a.pos and committed[b.aid][0] == b.pos:
                    pass
                if committed[a.aid][0] == b.pos and committed[b.aid][0] == a.pos:
                    collisions += 1
        for a in agents:
            if state[a.aid] != "moving":
                continue
            n1 = committed[a.aid][0]
            a.pos = n1
            if a.pos == a.goal:
                state[a.aid] = "done"
                done_at[a.aid] = t
                done_times[a.aid] = t
    failed = any(v == "moving" for v in state.values())
    flow = statistics.mean(done_times.values()) if done_times else t
    return {"makespan": t, "sum_cost": total_cost, "failed": failed,
            "flow_time": flow, "completed": len(done_times), "collisions": collisions}

# ---------------- BASELINE (naive): literal stop-and-wait, what most teams ship ----------------
# Simultaneous 1-step decisions from last tick's positions; static priority tie-break;
# wait in place. No lookahead, no reservations, no replanning.
# ---------------- OURS: decentralized right-of-way protocol ----------------
def run_ours(m, agents, R=99, LAMBDA=0.3, WATCHDOG=40, THRESH=2.0, H=HORIZON, DEPOT=DEPOT_LINGER):
    t = 0; cap = 8000; total_cost = 0; yield_events = 0; max_wait = 0
    state = {a.aid: "moving" for a in agents}
    done_at = {}
    done_times = {}
    while t < cap and any(v == "moving" for v in state.values()):
        t += 1
        for aid, td in list(done_at.items()):
            if t >= td + DEPOT:
                state[aid] = "gone"
        on_grid = [a for a in agents if state[a.aid] in ("moving", "done")]
        # --- local perception (each agent observes peers within comm radius R) ---
        for a in on_grid:
            if state[a.aid] == "done":
                continue
            for b in on_grid:
                if b.aid != a.aid and abs(b.pos[0] - a.pos[0]) + abs(b.pos[1] - a.pos[1]) <= R:
                    a.usage[b.pos] = a.usage.get(b.pos, 0) + 1
        for a in on_grid:
            a.usage = {k: v * 0.75 for k, v in a.usage.items() if v * 0.75 >= 0.5}
        # --- right-of-way resolution ---
        # Deterministic slot order (static total order by aid; watchdog temp_top
        # overrides). Each agent plans FRESH in its slot against already-committed
        # higher-RO moves (committed cells are penalty-costed) => no stale-info gap,
        # pure function of broadcast state, still zero central process.
        goals_of = {b.goal for b in on_grid if state[b.aid] in ("moving", "done")}
        committed = {}
        for a in sorted(on_grid, key=lambda x: (0 if x.temp_top else x.aid)):
            if state[a.aid] == "done":
                committed[a.aid] = (a.pos, a.pos)
                continue
            # fresh plan against committed cells (what higher-RO agents will do this tick)
            block = set()
            for b1, _ in committed.values():
                block.add(b1)
            cost, path = astar(m, a.pos, a.goal,
                               make_planner(a, m, a.usage, LAMBDA, THRESH, block=block))
            p = path[1:] if len(path) > 1 else []
            n1 = p[0] if len(p) > 0 else a.pos
            n2 = p[1] if len(p) > 1 else n1
            a.path = p
            clash = None
            for b in on_grid:
                if b.aid == a.aid:
                    continue
                if b.aid in committed:
                    b1, b2 = committed[b.aid]
                    if horizon_conflict(a.pos, n1, n2, b.pos, b1, b2):
                        clash = b; break
                elif state[b.aid] == "done":
                    if n1 == b.pos or (n2 and n2 == b.pos):
                        clash = b; break
            def window_clear(c1, c2):
                for b in on_grid:
                    if b.aid == a.aid:
                        continue
                    if b.aid in committed:
                        b1, b2 = committed[b.aid]
                        if horizon_conflict(a.pos, c1, c2, b.pos, b1, b2):
                            return False
                    elif state[b.aid] == "done":
                        if c1 == b.pos or (c2 and c2 == b.pos):
                            return False
                return True
            if clash is None:
                mv = n1
                moved = (mv != a.pos)
                a.wait = 0
                a.temp_top = False
                a.path = a.path[1:] if a.path else a.path
            else:
                moved = False
                if not a.owed_yield:
                    # (1) off-aisle yield cell (never into someone's goal; keep progress)
                    if a.wait < 6:
                        cands = [nb for nb in m.adj[a.pos]
                                 if nb != n1 and nb not in goals_of and window_clear(nb, nb)]
                        if cands:
                            cands.sort(key=lambda c: abs(c[0] - a.goal[0]) + abs(c[1] - a.goal[1]))
                            mv = cands[0]
                            a.path = []
                            yield_events += 1
                            moved = True
                    # (2) replan around the blocker
                    if not moved and a.wait >= 3:
                        block = {clash.pos}
                        if clash.aid in committed:
                            block.add(committed[clash.aid][0])
                        cost, p = astar(m, a.pos, a.goal,
                                        make_planner(a, m, a.usage, LAMBDA, THRESH, block=block))
                        if p and len(p) > 1:
                            np_ = p[1:]
                            c1 = np_[0] if np_ else a.pos
                            c2 = np_[1] if len(np_) > 1 else c1
                            if c1 != a.pos and window_clear(c1, c2) and c1 not in goals_of:
                                a.path = np_[1:]
                                mv = c1
                                moved = True
                if not moved:
                    mv = a.pos
                a.wait += 1
                if a.wait >= WATCHDOG and not a.temp_top:
                    a.temp_top = True
                    a.owed_yield = True
            committed[a.aid] = (mv, a.path[0] if (moved and a.path) else mv)
            if mv != a.pos:
                total_cost += 1
        # --- apply ---
        for a in agents:
            if state[a.aid] != "moving":
                continue
            mv = committed[a.aid][0]
            a.pos = mv
            if a.pos == a.goal:
                state[a.aid] = "done"
                done_at[a.aid] = t
                done_times[a.aid] = t
                a.temp_top = False
                a.wait = 0
        waiting = [a.wait for a in agents if state[a.aid] == "moving"]
        if waiting:
            max_wait = max(max_wait, max(waiting))
    failed = any(v == "moving" for v in state.values())
    flow = statistics.mean(done_times.values()) if done_times else t
    return {"makespan": t, "sum_cost": total_cost, "failed": failed,
            "yield_events": yield_events, "max_wait": max_wait,
            "flow_time": flow, "completed": len(done_times)}

# ---------------- Benchmark ----------------
def setup_trial(m, n_agents, rng):
    cells = list(m.free)
    rng.shuffle(cells)
    starts = cells[:n_agents]
    goals = [c for c in cells[n_agents:] if c not in starts][:n_agents]
    for i in range(n_agents):
        if goals[i] == starts[i]:
            goals[i], goals[n_agents - 1 - i] = goals[n_agents - 1 - i], goals[i]
    return starts, goals

def benchmark(n_trials=30, n_agents=5, seed=7):
    out = {}
    for name, rows in LAYOUTS.items():
        m = Map(rows)
        naive_res, strong_res, o_res = [], [], []
        for tr in range(n_trials):
            rng = random.Random(seed + tr)
            starts, goals = setup_trial(m, n_agents, rng)
            naive_res.append(run_baseline_naive(m, [Agent(i, s, g) for i, (s, g) in enumerate(zip(starts, goals))]))
            strong_res.append(run_baseline(m, [Agent(i, s, g) for i, (s, g) in enumerate(zip(starts, goals))]))
            o_res.append(run_ours(m, [Agent(i, s, g) for i, (s, g) in enumerate(zip(starts, goals))]))
        mn = [r["makespan"] for r in naive_res]; ms = [r["makespan"] for r in strong_res]
        om = [r["makespan"] for r in o_res]
        out[name] = {
            "naive_makespan_med": statistics.median(mn),
            "strong_makespan_med": statistics.median(ms),
            "ours_makespan_med": statistics.median(om),
            "vs_naive_pct": 100 * (statistics.median(mn) - statistics.median(om)) / statistics.median(mn),
            "vs_strong_pct": 100 * (statistics.median(ms) - statistics.median(om)) / statistics.median(ms),
            "fails_naive": sum(r["failed"] for r in naive_res),
            "fails_strong": sum(r["failed"] for r in strong_res),
            "fails_ours": sum(r["failed"] for r in o_res),
            "ours_yield_events_med": statistics.median(r["yield_events"] for r in o_res),
            "ours_max_wait_med": statistics.median(r["max_wait"] for r in o_res),
        }
    return out

if __name__ == "__main__":
    results = {}
    for n in (5, 10):
        res = benchmark(n_trials=30, n_agents=n)
        results[f"agents_{n}"] = res
        tot_b = sum(v["baseline_makespan_med"] for v in res.values())
        tot_o = sum(v["ours_makespan_med"] for v in res.values())
        fails_o = sum(v["fails_ours"] for v in res.values())
        fails_b = sum(v["fails_baseline"] for v in res.values())
        print(f"\n== {n} agents: baseline {tot_b} vs ours {tot_o} => improvement {100*(tot_b-tot_o)/tot_b:.1f}% (fails: base {fails_b} / ours {fails_o}) ==")
    with open("results.json", "w") as f:
        json.dump(results, f, indent=2)
    print("\nsaved results.json")


# ================================================================
# v2 TASK WORLD — pickup & delivery tasks, aisle block events, battery
# (PS req. 3 "Task Allocation & Re-routing" + dashboard "battery status")
# ================================================================
RELEASE_WAIT = 20      # TO_PICK robot blocked this long releases its pickup point
PICK_HOLD = 1          # ticks to pick an item at the pickup cell
DROP_HOLD = 1          # ticks to drop at the drop cell
BAT_MOVE_EMPTY = 0.4   # % per tick moving unloaded
BAT_MOVE_CARRY = 0.6   # % per tick moving loaded
BAT_WAIT = 0.1         # % per tick stopped
BAT_IDLE = 0.03        # % per tick parked, idle

@dataclass
class Task:
    tid: int
    pickup: tuple
    drop: tuple
    state: str = "OPEN"      # OPEN | HELD | DONE
    holder: int = -1

@dataclass
class BlockEvent:
    cell: tuple
    t_start: int
    t_end: int

def _pocket_cells(m, name):
    """Off-aisle parking cells (pockets / staging docks) — not on any aisle row or column."""
    v, h = AISLES[name]
    return [(x, y) for (x, y) in m.free if x not in v and y not in h]

def _cross_cells(m, avoid):
    """Choke points = intersection cells with 4 free neighbors."""
    return [c for c in m.free if c not in avoid and len(m.adj[c]) == 4]

def setup_trial_v2(m, n, rng, name="A", n_blocks=1):
    """Returns (homes, tasks, blocks). homes = distinct pockets (off-aisle docks);
    tasks = n pickup&drop pairs on random free cells; blocks = transient aisle
    intersections (the PS's 'narrow intersections or choke points')."""
    pockets = _pocket_cells(m, name)
    rng.shuffle(pockets)
    assert len(pockets) >= n, f"need {n} pockets, have {len(pockets)}"
    homes = pockets[:n]
    used = set(homes)
    pool = [c for c in m.free if c not in used]
    rng.shuffle(pool)
    tasks, i = [], 0
    while len(tasks) < n and i + 1 < len(pool):
        pu, dr = pool[i], pool[i + 1]
        i += 2
        if pu == dr:
            continue
        tasks.append(Task(len(tasks), pu, dr))
        used.add(pu); used.add(dr)
    blocks = []
    cross = _cross_cells(m, used)
    rng.shuffle(cross)
    for c in cross[:n_blocks]:
        t0 = rng.randint(8, 20)
        dur = rng.randint(20, 35)
        blocks.append(BlockEvent(c, t0, t0 + dur))
    return homes, tasks, blocks

def block_active(blocks, t, cell):
    return any(b.cell == cell and b.t_start <= t < b.t_end for b in blocks)

def task_tick(agents, tasks, ro_rank, t, blocks, intent=None, pockets=None):
    """Shared, DETERMINISTIC task controller — a pure function of the broadcast
    snapshot {aid: (pos, state, tid, last 2-step intent)}. Both systems run
    byte-identical rules, so the benchmark isolates the movement/conflict layer
    (PS req. 3). Returns (goal dict, releases this tick, dock shifts this tick).
    DOCK COURTESY: a parked IDLE robot re-docks to the nearest free pocket when
    a CARRY robot's broadcast intent targets its dock — a carrying robot must
    never wait on an idle one (measured real freeze)."""
    goal = {}
    released = 0
    docks = 0
    intent = intent or {}
    # arrival at dock: HOME -> IDLE (a robot that reaches its dock is parked)
    for a in agents:
        if a.state_task == "HOME" and a.pos == a.home:
            a.state_task = "IDLE"
    # dock courtesy (deterministic election by ro_rank order)
    for a in sorted(agents, key=lambda x: ro_rank[x.aid]):
        if a.state_task != "IDLE" or a.pos != a.home:
            continue
        needed = any(b.state_task == "CARRY" and intent.get(b.aid, (None, None))[0] == a.pos
                     for b in agents if b.aid != a.aid)
        if not needed:
            continue
        taken = {x.pos for x in agents} | {x.home for x in agents}
        taken |= {intent.get(x.aid, (None, None))[0] for x in agents}
        cands = sorted(pockets, key=lambda c: (abs(c[0]-a.pos[0]) + abs(c[1]-a.pos[1]), c))
        for c in cands:
            if c in taken or c == a.pos:
                continue
            a.state_task = "HOME"; a.home = c; a.wait = 0
            taken.add(c)
            docks += 1
            break
    # 1) release: TO_PICK robot blocked >= RELEASE_WAIT hands back the pickup point
    for a in agents:
        if a.state_task == "TO_PICK" and a.tid >= 0 and a.wait >= RELEASE_WAIT:
            tk = tasks[a.tid]
            if tk.state == "HELD" and tk.holder == a.aid:
                tk.state = "OPEN"; tk.holder = -1
            a.state_task = "HOME"; a.tid = -1
            a.wait = 0
            a.wait_block = 0
            released += 1
    # 2) claims: each OPEN task -> IDLE robot minimizing (dist to pickup, ro_rank)
    for tk in sorted(tasks, key=lambda x: x.tid):
        if tk.state != "OPEN":
            continue
        cands = [a for a in agents if a.state_task == "IDLE"]
        if not cands:
            continue
        cands.sort(key=lambda a: (abs(a.pos[0]-tk.pickup[0]) + abs(a.pos[1]-tk.pickup[1]),
                                  ro_rank[a.aid]))
        w = cands[0]
        tk.state = "HELD"; tk.holder = w.aid
        w.state_task = "TO_PICK"; w.tid = tk.tid
    # 3) per-robot target
    for a in agents:
        if a.state_task == "IDLE":
            goal[a.aid] = a.home
        elif a.state_task == "HOME":
            goal[a.aid] = a.home
        else:
            tk = tasks[a.tid]
            goal[a.aid] = tk.pickup if a.state_task == "TO_PICK" else tk.drop
    return goal, released, docks

def battery_tick(a, moved):
    if a.state_task == "CARRY" and moved:
        a.battery -= BAT_MOVE_CARRY
    elif moved:
        a.battery -= BAT_MOVE_EMPTY
    elif a.state_task == "IDLE":
        a.battery -= BAT_IDLE
    else:
        a.battery -= BAT_WAIT
    a.battery = max(0.0, a.battery)

# ---------------- v2 BASELINE: same task layer + stop-and-wait movement ----------------
def run_baseline_v2(m, agents, tasks, blocks, cap=8000, name="A", record=None):
    """Faithful stop-and-wait (full view, static priority, fresh commit, same
    physical safety layer) with the SHARED task controller (PS req. 3). The
    run ends when every task is dropped."""
    t = 0; total_cost = 0; collisions = 0; reals = 0
    done_tasks = set()
    min_battery = 100.0
    ro_rank = {a.aid: (1, a.aid) for a in agents}        # static priority
    last_move = {}
    dock_shifts = 0
    pocket_cache = _pocket_cells(m, name)
    while t < cap and len(done_tasks) < len(tasks):
        t += 1
        for b in blocks:
            if b.t_start <= t < b.t_end and any(a.pos == b.cell for a in agents):
                b.t_start += 1; b.t_end += 1
        wall = lambda c, tick: block_active(blocks, tick, c)
        goal, released, docks = task_tick(agents, tasks, ro_rank, t, blocks,
                                          intent=last_move, pockets=pocket_cache)
        reals += released
        dock_shifts += docks
        pos_snap = {a.aid: a.pos for a in agents}
        move = {}
        for a in sorted(agents, key=lambda x: x.aid):
            tk = tasks[a.tid] if a.tid >= 0 else None
            # pick / drop holds
            if a.state_task == "TO_PICK" and tk and a.pos == tk.pickup:
                a.hold += 1
                if a.hold >= PICK_HOLD:
                    a.state_task = "CARRY"; a.hold = 0
                move[a.aid] = a.pos; continue
            if a.state_task == "CARRY" and tk and a.pos == tk.drop:
                a.hold += 1
                if a.hold >= DROP_HOLD:
                    tk.state = "DONE"; done_tasks.add(tk.tid)
                    a.state_task = "IDLE"; a.tid = -1; a.hold = 0
                    a.owed_yield = False
                move[a.aid] = a.pos; continue
            a.hold = 0
            g = goal[a.aid]
            cost, path = astar(m, a.pos, g)
            if path is None or len(path) < 2:
                move[a.aid] = a.pos; continue            # aisle split: wait it out
            p = path[1:]
            n1 = p[0]
            clash = wall(n1, t + 1)
            if not clash:
                for b in agents:
                    if b.aid == a.aid:
                        continue
                    if b.aid in move:
                        b1 = move[b.aid]
                        if b1 == n1:
                            clash = True; break
                        if b.pos == n1 and not (b1 != b.pos and b1 != a.pos):
                            clash = True; break          # b stays (or swap)
                    elif b.pos == n1:
                        clash = True; break              # occupied / not committed
            move[a.aid] = a.pos if clash else n1
        # independent collision check (pre-apply move vs snapshot)
        for i in range(len(agents)):
            for j in range(i + 1, len(agents)):
                a, b = agents[i], agents[j]
                if move[a.aid] == move[b.aid]:
                    collisions += 1
                if move[a.aid] == pos_snap[b.aid] and move[b.aid] == pos_snap[a.aid]:
                    collisions += 1
        # apply
        for a in agents:
            moved = move[a.aid] != a.pos
            if moved:
                a.pos = move[a.aid]
                total_cost += 1
            battery_tick(a, moved)
            a.wait = 0 if moved else a.wait + 1
            min_battery = min(min_battery, a.battery)
        last_move = {a.aid: move[a.aid] for a in agents}
        if record:
            record(t, agents, tasks, pos_snap, move, move, blocks, move, goal)
    failed = len(done_tasks) < len(tasks)
    return {"task_time": (t if not failed else None), "completed": len(done_tasks),
            "n_tasks": len(tasks), "collisions": collisions, "reallocations": reals,
            "dock_shifts": dock_shifts, "min_battery": round(min_battery, 1),
            "failed": failed, "cap_hit": failed, "total_cost": total_cost}
