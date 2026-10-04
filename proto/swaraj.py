"""
SwarajFleet — TRULY decentralized, parallel decision-making protocol.

Every tick, all agents decide SIMULTANEOUSLY (no sequential slot, no coordinator):
  1. PERCEIVE  : read neighbors' positions + 2-step intents from the last broadcast
  2. PLAN      : congestion-aware A* to own goal (local, on the "edge")
  3. DECIDE    : if the planned move conflicts with a higher-RO neighbor's intent,
                 yield (step aside to a free cell) or wait; otherwise commit
  4. BROADCAST : publish own position + 2-step intent for the next tick

Right-of-way = deterministic total order (static by aid, watchdog override) =>
deadlock-free by construction. Congestion cost learned from observed local traffic.
This is the architecture the PS asks for: intelligence on each AMR, peers only.
"""
import statistics, os
from sim import (Map, LAYOUTS, Agent, astar, make_planner, setup_trial,
                 horizon_conflict, DEPOT_LINGER)
import random, json

DEBUG = os.environ.get("SW_DEBUG") == "1"
# A/B switch (Phase 13): "rot" = locked rotating order (control).
# "dist" = goal-distance dominant within tier 3 (rotating order kept as the
# unique tie-break). Total order remains unique in both modes.
RO_MODE = os.environ.get("SWARAJ_RO", "rot")


def run_swaraj(m, agents, R=99, LAMBDA=0.5, WATCHDOG=30, THRESH=2.0,
               H=2, DEPOT=DEPOT_LINGER, seed=None):
    """Truly parallel decentralized execution. Returns metrics dict."""
    t = 0; cap = 8000; total_cost = 0; yield_events = 0; max_wait = 0
    collisions = 0
    state = {a.aid: "moving" for a in agents}
    done_at = {}; done_times = {}
    # last broadcast intents: aid -> (n1, n2)
    last_intent = {}
    while t < cap and any(v == "moving" for v in state.values()):
        t += 1
        for aid, td in list(done_at.items()):
            if t >= td + DEPOT:
                state[aid] = "gone"
        on_grid = [a for a in agents if state[a.aid] in ("moving", "done")]

        # ---- PERCEIVE: update congestion from current positions (local view) ----
        for a in on_grid:
            if state[a.aid] == "done":
                continue
            for b in on_grid:
                if b.aid != a.aid and abs(b.pos[0]-a.pos[0]) + abs(b.pos[1]-a.pos[1]) <= R:
                    a.usage[b.pos] = a.usage.get(b.pos, 0) + 1
        for a in on_grid:
            a.usage = {k: v*0.75 for k, v in a.usage.items() if v*0.75 >= 0.5}

        goals_of = {b.goal for b in on_grid}
        # positions and last intents of everyone (the broadcast snapshot)
        pos_snap = {a.aid: a.pos for a in on_grid}
        # each agent computes its candidate move INDEPENDENTLY from the snapshot
        cand = {}      # aid -> (n1, n2)
        for a in on_grid:
            if state[a.aid] == "done":
                cand[a.aid] = (a.pos, a.pos)
                continue
            cost, path = astar(m, a.pos, a.goal,
                               make_planner(a, m, a.usage, LAMBDA, THRESH))
            p = path[1:] if len(path) > 1 else []
            n1 = p[0] if len(p) > 0 else a.pos
            n2 = p[1] if len(p) > 1 else n1
            a.path = p
            cand[a.aid] = (n1, n2)

        # ---- DECIDE (parallel, two passes — pure functions of the broadcast snapshot) ----
        # RO order rotates every EPOCH ticks: a static "A always yields to B" cycle
        # cannot persist; fairness rotates automatically.
        epoch = t // 25
        N = len(agents)
        # UNIQUE total order (tuple keys): (0,aid) for temp_top, else
        # (1,(aid-epoch) mod N). Modulo by total N (not #moving) keeps values
        # distinct — rank ties previously made both robots skip the conflict
        # check against each other => simultaneous move into one cell.
        ro_rank = {a.aid: ((0, a.aid) if a.temp_top else (1, (a.aid - epoch) % N))
                   for a in on_grid}
        move = {}

        # PASS 1: conservative conflict map (no following privilege) — identical
        # for every agent since it depends only on the snapshot.
        in_conflict = {}
        for a in on_grid:
            if state[a.aid] == "done":
                in_conflict[a.aid] = False
                continue
            n1, n2 = cand[a.aid]
            mc = False
            for b in on_grid:
                if b.aid == a.aid or state[b.aid] == "done":
                    continue
                b1, b2 = cand[b.aid]
                if n1 == pos_snap[b.aid]:
                    mc = True; break
                if ro_rank[b.aid] >= ro_rank[a.aid]:
                    continue
                if (n1 == b1 or (n2 and (n2 == b2 or (n2 == b1 and b2 == n1)))):
                    mc = True; break
            in_conflict[a.aid] = mc

        # PASS 2: decide. A robot may FOLLOW a conflict-free robot ahead (whose
        # t+1 cell is exactly its broadcast b1); otherwise yield or wait.
        for a in on_grid:
            if state[a.aid] == "done":
                move[a.aid] = a.pos
                continue
            n1, n2 = cand[a.aid]
            if not in_conflict[a.aid]:
                move[a.aid] = n1
                a.wait = 0
                a.temp_top = False
                a.path = a.path[1:] if a.path else a.path
                continue
            # classify the conflict: occupancy (may follow) vs RO reservation (must yield)
            blocked_by_ro = False
            for b in on_grid:
                if b.aid == a.aid or state[b.aid] == "done":
                    continue
                if ro_rank[b.aid] >= ro_rank[a.aid]:
                    continue
                b1, b2 = cand[b.aid]
                if (n1 == b1 or (n2 and (n2 == b2 or (n2 == b1 and b2 == n1)))):
                    blocked_by_ro = True
                    break
            can_follow = not blocked_by_ro
            if can_follow:
                for b in on_grid:
                    if b.aid == a.aid or state[b.aid] == "done":
                        continue
                    if n1 == pos_snap[b.aid]:
                        b1, _ = cand[b.aid]
                        if in_conflict[b.aid] or b1 == pos_snap[b.aid] or b1 == a.pos:
                            can_follow = False
                            break
            if can_follow:
                move[a.aid] = n1
                a.wait = 0
                a.temp_top = False
                a.path = a.path[1:] if a.path else a.path
                continue
            # yield: step aside to a free, safe cell; else wait
            moved = False
            if not a.owed_yield and a.wait < 6:
                for nb in sorted(m.adj[a.pos],
                                 key=lambda c: abs(c[0]-a.goal[0])+abs(c[1]-a.goal[1])):
                    if nb == n1 or nb in goals_of:
                        continue
                    # t+1 safety only: free of everyone's current pos and t+1
                    # destination. (t+2 is handled by the 2-step window next tick;
                    # reserving t+2 here caused mutual over-caution stalemates.)
                    if any(nb == pos_snap[x.aid] for x in on_grid):
                        continue
                    if any(nb == cand[x.aid][0] for x in on_grid if state[x.aid] == "moving"):
                        continue
                    # RO claim protection: no higher-RO conflicting robot may also
                    # be able to yield into this cell (parallel double-booking)
                    if any(ro_rank[x.aid] < ro_rank[a.aid] and in_conflict.get(x.aid)
                           and nb in m.adj[x.pos]
                           for x in on_grid if state[x.aid] == "moving"):
                        continue
                    move[a.aid] = nb
                    a.path = []
                    yield_events += 1
                    moved = True
                    break
            if not moved:
                move[a.aid] = a.pos
            a.wait += 1
            if a.wait >= WATCHDOG and not a.temp_top:
                a.temp_top = True
                a.owed_yield = True

        # ---- SAFETY CHECK (independent of the protocol): detect any vertex/edge conflict ----
        moving_a = [a for a in on_grid if state[a.aid] == "moving"]
        for i in range(len(moving_a)):
            for j in range(i + 1, len(moving_a)):
                a, b = moving_a[i], moving_a[j]
                if move[a.aid] == move[b.aid]:
                    collisions += 1                                   # vertex
                    if DEBUG:
                        print(f"[V t={t}] a{a.aid} {pos_snap[a.aid]}->{move[a.aid]} a{b.aid} {pos_snap[b.aid]}->{move[b.aid]} "
                              f"RO {ro_rank.get(a.aid)}/{ro_rank.get(b.aid)} infc {in_conflict.get(a.aid)}/{in_conflict.get(b.aid)} "
                              f"plan {cand[a.aid]}/{cand[b.aid]} owed {a.owed_yield}/{b.owed_yield} wait {a.wait}/{b.wait}")
                if move[a.aid] == pos_snap[b.aid] and move[b.aid] == pos_snap[a.aid]:
                    collisions += 1                                   # edge swap
                    if DEBUG:
                        print(f"[E t={t}] a{a.aid} {pos_snap[a.aid]}->{move[a.aid]} a{b.aid} {pos_snap[b.aid]}->{move[b.aid]} "
                              f"RO {ro_rank.get(a.aid)}/{ro_rank.get(b.aid)} infc {in_conflict.get(a.aid)}/{in_conflict.get(b.aid)} "
                              f"plan {cand[a.aid]}/{cand[b.aid]} owed {a.owed_yield}/{b.owed_yield} wait {a.wait}/{b.wait}")
        # ---- APPLY (simultaneous) + BROADCAST ----
        for a in on_grid:
            if state[a.aid] != "moving":
                continue
            a.pos = move[a.aid]
            if a.pos == a.goal:
                state[a.aid] = "done"; done_at[a.aid] = t; done_times[a.aid] = t
                a.temp_top = False; a.wait = 0
            if move[a.aid] != pos_snap[a.aid]:
                total_cost += 1
            # broadcast next intent (recompute cheap 2-step)
            cost, path = astar(m, a.pos, a.goal)
            p = path[1:] if len(path) > 1 else []
            last_intent[a.aid] = (p[0] if p else a.pos, p[1] if len(p) > 1 else (p[0] if p else a.pos))
        waiting = [a.wait for a in agents if state[a.aid] == "moving"]
        if waiting:
            max_wait = max(max_wait, max(waiting))
    failed = any(v == "moving" for v in state.values())
    flow = statistics.mean(done_times.values()) if done_times else t
    return {"makespan": t, "sum_cost": total_cost, "failed": failed,
            "yield_events": yield_events, "max_wait": max_wait,
            "flow_time": flow, "completed": len(done_times), "collisions": collisions}




# ================================================================
# v2: task world (pickup & delivery) + shared task controller
# PS req. 3: task allocation & re-routing; dashboard: battery
# ================================================================
from sim import (setup_trial_v2, task_tick, block_active, battery_tick,
                 PICK_HOLD, DROP_HOLD, Task, RELEASE_WAIT, _pocket_cells)


def _finish_metrics(t, done_tasks, tasks, collisions, reals, min_battery,
                    extra=None):
    failed = len(done_tasks) < len(tasks)
    out = {"task_time": (t if not failed else None), "completed": len(done_tasks),
           "n_tasks": len(tasks), "collisions": collisions,
           "reallocations": reals, "min_battery": round(min_battery, 1),
           "failed": failed, "cap_hit": failed}
    if extra:
        out.update(extra)
    return out


def _apply_and_bookkeep(agents, move, pos_snap, blocks, t):
    """Shared apply step: move, battery, wait semantics, cost accounting.
    Returns total_cost delta. wait resets on any position change, else +=1."""
    cost = 0
    for a in agents:
        moved = move[a.aid] != a.pos
        if moved:
            a.pos = move[a.aid]
        battery_tick(a, moved)
        a.hist = (a.hist[-5:] + (a.pos,))
        if a.state_task == "IDLE" and a.pos == a.home:
            a.wait = 0            # parked at dock: not stuck
        else:
            a.wait = 0 if moved else a.wait + 1
        if moved:
            cost += 1
    return cost


def _has_yield_cell(b, agents, holding, cand, pos_snap, goals_of, m, wall, t,
                    in_conflict, ro_rank):
    """Does conflicted robot b have a legal yield cell this tick? (Same checks
    as the yield branch — makes 'the occupant will leave' deterministic.)"""
    for nb in sorted(m.adj[b.pos], key=lambda c: c):
        if nb == cand[b.aid][0] or wall(nb, t + 1):
            continue
        if any(nb == pos_snap[x.aid] for x in agents):
            continue
        if any(nb == cand[x.aid][0] for x in agents if not holding[x.aid]):
            continue
        if nb in goals_of:
            continue
        if any(nb in m.adj[y.pos] and in_conflict[y.aid]
               and ro_rank[y.aid] < ro_rank[b.aid] for y in agents):
            continue
        return True
    return False


def run_swaraj_v2(m, agents, tasks, blocks, R=99, LAMBDA=0.5, WATCHDOG=30,
                  THRESH=2.0, cap=8000, name="A", record=None, drop=None):
    """SwarajFleet protocol on the task world. Same 5 invariants; plus the
    shared deterministic task controller (identical to the baseline's), so the
    comparison isolates the conflict-resolution layer.

    drop=(aid, t1, t2): PS-background Wi-Fi dead-zone mode. During [t1, t2)
    robot `aid`'s broadcast is frozen at its last-known state (peers cannot
    see its live position/intent); the robot itself keeps running on its own
    local view. A LOCAL SAFETY LAYER (physical — every real AMR carries an
    onboard sensor stop) backstops the blind spot: nobody steps into a cell
    occupied in ground truth. Counted as safety_stops; never hidden."""
    t = 0; yield_events = 0; max_wait = 0; collisions = 0; reals = 0
    done_tasks = set()
    min_battery = 100.0
    dock_shifts = 0
    safety_stops = 0
    drop_ghost = None
    last_intent = {}
    pocket_cache = _pocket_cells(m, name)
    while t < cap and len(done_tasks) < len(tasks):
        t += 1
        # a block only starts once its cell is free
        for b in blocks:
            if b.t_start <= t < b.t_end and any(a.pos == b.cell for a in agents):
                b.t_start += 1; b.t_end += 1
        wall = lambda c, tick: block_active(blocks, tick, c)

        # ---- task layer (shared, deterministic peer election) ----
        n_m = max(1, len(agents))
        epoch = t // 25
        if RO_MODE == "dist":
            def _gdist(a):
                tgt = a.home
                if a.tid >= 0:
                    tk = tasks[a.tid]
                    tgt = tk.pickup if a.state_task == "TO_PICK" else tk.drop
                return abs(a.pos[0] - tgt[0]) + abs(a.pos[1] - tgt[1])
            ro_rank = {a.aid: ((0, a.aid) if a.temp_top
                         else (1, a.aid) if a.state_task == "CARRY"
                         else (2, _gdist(a), (a.aid - epoch) % n_m))
                       for a in agents}
        else:
            ro_rank = {a.aid: ((0, a.aid) if a.temp_top
                         else (1, a.aid) if a.state_task == "CARRY"
                         else (2, (a.aid - epoch) % n_m))
                       for a in agents}
        goal, released, docks = task_tick(agents, tasks, ro_rank, t, blocks,
                                          intent=last_intent, pockets=pocket_cache)
        reals += released
        dock_shifts += docks

        # ---- PERCEIVE (congestion from current positions) ----
        for a in agents:
            for b in agents:
                if b.aid != a.aid and abs(b.pos[0]-a.pos[0]) + abs(b.pos[1]-a.pos[1]) <= R:
                    a.usage[b.pos] = a.usage.get(b.pos, 0) + 1
        for a in agents:
            a.usage = {k: v*0.75 for k, v in a.usage.items() if v*0.75 >= 0.5}

        pos_snap = {a.aid: a.pos for a in agents}
        # dead-zone: peers see D's LAST-KNOWN state (frozen broadcast)
        if drop:
            da, d1, d2 = drop
            if d1 <= t < d2:
                if drop_ghost is None:
                    drop_ghost = pos_snap[da]
                pos_snap[da] = drop_ghost
        # sticky cells = where parked/returning robots WILL STAY (their dock).
        # Active robots' pickup/drop targets are transit — they leave on
        # arrival — so they must not block yield cells (measured CARRY
        # head-on freeze otherwise).
        goals_of = {goal[a.aid] for a in agents if a.state_task in ("IDLE", "HOME")}

        # ---- PLAN (fresh congestion A* to the task target; blocked cell = wait) ----
        cand, holding = {}, {}
        for a in agents:
            holding[a.aid] = False
            tk = tasks[a.tid] if a.tid >= 0 else None
            if a.state_task == "TO_PICK" and tk and a.pos == tk.pickup:
                a.hold += 1
                if a.hold >= PICK_HOLD:
                    a.state_task = "CARRY"; a.hold = 0
                cand[a.aid] = (a.pos, a.pos); holding[a.aid] = True
                continue
            if a.state_task == "CARRY" and tk and a.pos == tk.drop:
                a.hold += 1
                if a.hold >= DROP_HOLD:
                    tk.state = "DONE"; done_tasks.add(tk.tid)
                    a.state_task = "IDLE"; a.tid = -1; a.hold = 0
                    a.owed_yield = False
                cand[a.aid] = (a.pos, a.pos); holding[a.aid] = True
                continue
            a.hold = 0
            g = goal[a.aid]
            base = make_planner(a, m, a.usage, LAMBDA, THRESH)
            def cfn(cell, _b=base, _t=t):
                c = _b(cell)
                if wall(cell, _t + 1):
                    c += 1e6
                return c
            cost, path = astar(m, a.pos, g, cfn)
            if path is None or len(path) < 2:
                cand[a.aid] = (a.pos, a.pos); continue
            p = path[1:]
            n1, n2 = p[0], (p[1] if len(p) > 1 else p[0])
            if wall(n1, t + 1):
                cand[a.aid] = (a.pos, a.pos); continue
            # RE-ROUTE around a persistent blocker (PS req. 3: "changing paths
            # if one robot encounters a blocked aisle"). Congestion cost alone
            # is too weak to beat a long detour, so a robot blocked >= 8
            # straight ticks treats the blocking cell as a wall and plans
            # around it (e.g., out via the perimeter ring).
            if a.wait >= 8:
                def cfn2(cell, _b=base, _t=t, _w=n1):
                    c = _b(cell)
                    if wall(cell, _t + 1):
                        c += 1e6
                    if cell == _w:
                        c += 1e4
                    return c
                cost2, path2 = astar(m, a.pos, g, cfn2)
                if path2 and len(path2) > 1:
                    p2 = path2[1:]
                    nn1 = p2[0]
                    if nn1 != a.pos and not wall(nn1, t + 1) and nn1 != n1:
                        a.path = p2
                        cand[a.aid] = (nn1, p2[1] if len(p2) > 1 else nn1)
                        continue
            # ANTI-OSCILLATION: an X-Y-X-Y position pattern is a livelock,
            # never progress (measured in 3-robot corner cycles). Force a
            # one-tick stay — set in the BROADCAST intent so every peer sees
            # me as stationary (deciding it later broke their follow logic).
            if len(a.hist) >= 4:
                h = a.hist[-4:]
                if (h[0] == h[2] and h[1] == h[3] and h[0] != h[1]
                        and a.pos in (h[0], h[1]) and n1 in (h[0], h[1])):
                    cand[a.aid] = (a.pos, a.pos)
                    a.path = []
                    continue
            a.path = p
            cand[a.aid] = (n1, n2)

        # ---- SWAP RE-ROUTE: head-on pairs on a pocketless aisle ping-pong if
        # both yield backward (measured). A robot in a swap does NOT yield —
        # it plans around the swap partner's cell (detour, e.g., perimeter
        # ring). Sequential stage: the first robot detours, which un-swaps the
        # second (it then waits one tick and advances).
        for a in agents:
            if holding[a.aid]:
                continue
            n1, n2 = cand[a.aid]
            if n1 == a.pos:
                continue
            in_swap = any(pos_snap[b.aid] == n1 and cand[b.aid][0] == a.pos
                          for b in agents
                          if b.aid != a.aid and not holding[b.aid]
                          and cand[b.aid][0] != pos_snap[b.aid])
            if not in_swap:
                continue
            base = make_planner(a, m, a.usage, LAMBDA, THRESH)
            def cfn_swap(cell, _b=base, _t=t, _w=n1):
                c = _b(cell)
                if wall(cell, _t + 1):
                    c += 1e6
                if cell == _w:
                    c += 1e4
                return c
            cost2, path2 = astar(m, a.pos, goal[a.aid], cfn_swap)
            if not path2 or len(path2) < 2:
                continue
            p2 = path2[1:]
            nn1 = p2[0]
            if nn1 == a.pos or nn1 == n1 or wall(nn1, t + 1):
                continue
            if any(nn1 == pos_snap[x.aid] for x in agents):
                continue
            a.path = p2
            cand[a.aid] = (nn1, p2[1] if len(p2) > 1 else nn1)

        # ---- DECIDE (two passes, pure functions of the snapshot) ----
        in_conflict = {}
        for a in agents:
            if holding[a.aid]:
                in_conflict[a.aid] = False; continue
            n1, n2 = cand[a.aid]
            mc = False
            for b in agents:
                if b.aid == a.aid:
                    continue
                b1, b2 = cand[b.aid]
                if n1 == pos_snap[b.aid]:
                    mc = True; break
                if ro_rank[b.aid] >= ro_rank[a.aid]:
                    continue
                if (n1 == b1 or (n2 and (n2 == b2 or (n2 == b1 and b2 == n1)))):
                    mc = True; break
            in_conflict[a.aid] = mc

        move = {}
        for a in agents:
            if holding[a.aid]:
                move[a.aid] = a.pos; continue
            n1, n2 = cand[a.aid]
            if n1 == a.pos:
                # parked: normally wait — but a higher-priority CARRY robot
                # targeting my cell has claim (it proceeds expecting me to
                # vacate; measured collision otherwise).
                parked_must_clear = (a.state_task != "CARRY" and any(
                    y.state_task == "CARRY" and ro_rank[y.aid] < ro_rank[a.aid]
                    and cand[y.aid][0] == a.pos for y in agents))
                if parked_must_clear:
                    for nb in sorted(m.adj[a.pos],
                                     key=lambda c: abs(c[0]-a.home[0]) + abs(c[1]-a.home[1])):
                        if nb == n1 or wall(nb, t + 1):
                            continue
                        if any(nb == pos_snap[x.aid] for x in agents):
                            continue
                        if any(nb == cand[x.aid][0] for x in agents
                               if not holding[x.aid]
                               and cand[x.aid][0] != pos_snap[x.aid]):
                            continue
                        if nb in goals_of:
                            continue
                        if any(nb in m.adj[y.pos] and in_conflict[y.aid]
                               and ro_rank[y.aid] < ro_rank[a.aid] for y in agents):
                            continue
                        move[a.aid] = nb
                        a.path = []
                        yield_events += 1
                        break
                    else:
                        move[a.aid] = a.pos
                else:
                    move[a.aid] = a.pos
                continue
            if not in_conflict[a.aid]:
                move[a.aid] = n1; continue
            # conflicted: proceed to n1 only if I am the SOLE robot targeting it
            # (two followers into one freeing cell was a real measured
            # collision), and if it is occupied, the occupant must be
            # conflict-free and actually leaving (non-swap). A free,
            # uncontested cell is always enterable — my conflict may be at t+2,
            # which the next tick's window handles.
            # (self always counts: in this branch n1 is my own candidate move)
            targeters = [x for x in agents
                         if not holding[x.aid] and cand[x.aid][0] == n1
                         and (x is a or cand[x.aid][0] != pos_snap[x.aid])]
            # the MIN-RANK targeter (highest priority among those aiming at n1)
            # proceeds when n1 is free or deterministically freeing; the others
            # yield/wait. Requiring SOLE targeter caused a measured two-trains
            # ping-pong: both targeters yielded backward in the same tick,
            # forever.
            i_am_min = min(targeters, key=lambda x: ro_rank[x.aid]).aid == a.aid
            can_proceed = i_am_min
            if can_proceed:
                # self uses its TRUE position (its own broadcast may be stale to peers)
                occ = [x for x in agents if (x is a and a.pos == n1)
                       or (x is not a and pos_snap[x.aid] == n1)]
                if not occ:
                    pass                                   # free cell
                else:
                    b = occ[0]
                    b1, b2 = cand[b.aid]
                    if not in_conflict[b.aid]:
                        can_proceed = (b1 != pos_snap[b.aid] and b1 != a.pos)
                    else:
                        can_proceed = (a.state_task == "CARRY"
                                       and b.state_task != "CARRY"
                                       and ro_rank[a.aid] < ro_rank[b.aid]
                                       and _has_yield_cell(b, agents, holding, cand,
                                                           pos_snap, goals_of, m, wall,
                                                           t, in_conflict, ro_rank))
            if can_proceed:
                move[a.aid] = n1; continue
            # MUST-CLEAR: a non-carry robot whose cell a higher-priority CARRY
            # robot is targeting must yield (clears the measured carry-vs-idle
            # corridor ping-pong). Overrides the yield budget.
            must_clear = (a.state_task != "CARRY" and any(
                y.state_task == "CARRY" and ro_rank[y.aid] < ro_rank[a.aid]
                and cand[y.aid][0] == a.pos for y in agents))
            moved = False
            if ((not a.owed_yield and a.wait < 6) or a.wait >= 2 * WATCHDOG
                    or must_clear):
                for nb in sorted(m.adj[a.pos],
                                 key=lambda c: abs(c[0]-goal[a.aid][0]) + abs(c[1]-goal[a.aid][1])):
                    if nb == n1 or wall(nb, t + 1):
                        continue
                    if any(nb == pos_snap[x.aid] for x in agents):
                        continue
                    if any(nb == cand[x.aid][0] for x in agents if not holding[x.aid]):
                        continue
                    if nb in goals_of:
                        continue
                    if any(nb in m.adj[y.pos] and in_conflict[y.aid]
                           and ro_rank[y.aid] < ro_rank[a.aid] for y in agents):
                        continue
                    move[a.aid] = nb; a.path = []; yield_events += 1
                    moved = True; break
            if not moved:
                move[a.aid] = a.pos

        # ---- LOCAL SAFETY LAYER (physical, on every real AMR) ----
        # While D is in the dead zone, peers cannot see D's live position, so
        # the P2P reservation layer has a blind spot. The onboard sensor stop
        # (present on every real AMR) closes it: nobody steps into a cell
        # occupied in ground truth. Counted — never hidden.
        if drop:
            da, d1, d2 = drop
            if d1 <= t < d2:
                for a in agents:
                    tgt = move[a.aid]
                    if tgt != a.pos and any(b.pos == tgt for b in agents
                                             if b.aid != a.aid):
                        move[a.aid] = a.pos
                        safety_stops += 1

        # ---- SAFETY CHECK (independent) ----
        for i in range(len(agents)):
            for j in range(i + 1, len(agents)):
                a, b = agents[i], agents[j]
                if move[a.aid] == move[b.aid]:
                    collisions += 1
                    if DEBUG:
                        print(f"[V t={t}] a{a.aid} {pos_snap[a.aid]}->{move[a.aid]} a{b.aid} {pos_snap[b.aid]}->{move[b.aid]} "
                              f"RO {ro_rank[a.aid]}/{ro_rank[b.aid]} infc {in_conflict.get(a.aid)}/{in_conflict.get(b.aid)} "
                              f"plan {cand[a.aid]}/{cand[b.aid]} hold {holding[a.aid]}/{holding[b.aid]} "
                              f"task {a.state_task}/{b.state_task} wait {a.wait}/{b.wait}")
                if move[a.aid] == pos_snap[b.aid] and move[b.aid] == pos_snap[a.aid]:
                    collisions += 1
                    if DEBUG:
                        print(f"[E t={t}] a{a.aid} {pos_snap[a.aid]}->{move[a.aid]} a{b.aid} {pos_snap[b.aid]}->{move[b.aid]} "
                              f"RO {ro_rank[a.aid]}/{ro_rank[b.aid]} infc {in_conflict.get(a.aid)}/{in_conflict.get(b.aid)} "
                              f"plan {cand[a.aid]}/{cand[b.aid]} hold {holding[a.aid]}/{holding[b.aid]} "
                              f"task {a.state_task}/{b.state_task} wait {a.wait}/{b.wait}")

        # ---- APPLY + BROADCAST (publish 2-step intent for next tick) ----
        _apply_and_bookkeep(agents, move, pos_snap, blocks, t)
        for a in agents:
            min_battery = min(min_battery, a.battery)
            if a.wait >= WATCHDOG and not a.temp_top:
                a.temp_top = True; a.owed_yield = True
            if a.wait == 0:
                a.temp_top = False
            g = goal[a.aid]
            if not holding[a.aid] and not (
                    drop and a.aid == drop[0] and drop[1] <= t < drop[2]):
                cost, path = astar(m, a.pos, g)
                p = path[1:] if (path and len(path) > 1) else []
                last_intent[a.aid] = (p[0] if p else a.pos,
                                      p[1] if len(p) > 1 else (p[0] if p else a.pos))
        waiting = [a.wait for a in agents]
        if waiting:
            max_wait = max(max_wait, max(waiting))
        if record:
            record(t, agents, tasks, pos_snap, cand, move, blocks, last_intent, goal)

    return _finish_metrics(t, done_tasks, tasks, collisions, reals, min_battery,
                           extra={"yield_events": yield_events, "max_wait": max_wait,
                                  "dock_shifts": dock_shifts,
                                  "safety_stops": safety_stops})


if __name__ == "__main__":
    from sim import run_baseline_v2
    TR = 30
    results = {}
    for n in (5, 10):
        results[f"agents_{n}"] = {}
        print(f"\n===== {n} agents & {n} tasks, {TR} trials/layout (task world, blocked intersections) =====")
        for name, rows in LAYOUTS.items():
            m = Map(rows)
            sw, of = [], []
            for tr in range(TR):
                rng = random.Random(7 + tr)
                homes, tasks, blocks = setup_trial_v2(m, n, rng, name=name)
                ag_sw = [Agent(i, h, h, home=h) for i, h in enumerate(homes)]
                ag_of = [Agent(i, h, h, home=h) for i, h in enumerate(homes)]
                sw.append(run_baseline_v2(m, ag_sw, tasks, blocks, name=name))
                rng2 = random.Random(7 + tr)
                homes2, tasks2, blocks2 = setup_trial_v2(m, n, rng2, name=name)
                ag_of = [Agent(i, h, h, home=h) for i, h in enumerate(homes2)]
                of.append(run_swaraj_v2(m, ag_of, tasks2, blocks2, name=name))
            assert homes == homes2

            def stats(rs, n):
                ok = [r for r in rs if not r["failed"]]
                return dict(
                    ok_trials=len(ok), trials=TR,
                    task_time_med=(statistics.median([r["task_time"] for r in ok]) if ok else None),
                    collisions=sum(r["collisions"] for r in rs),
                    reallocations_med=(statistics.median([r["reallocations"] for r in ok]) if ok else None),
                    min_battery_med=(statistics.median([r["min_battery"] for r in ok]) if ok else None),
                    deadlocks=sum(1 for r in rs if r["failed"]),
                )
            S, O = stats(sw, n), stats(of, n)
            both = [(a, b) for a, b in zip(sw, of) if not a["failed"] and not b["failed"]]
            if both:
                hms = 100 * (statistics.median([a["task_time"] for a, _ in both])
                             - statistics.median([b["task_time"] for _, b in both])) / statistics.median([a["task_time"] for a, _ in both])
            else:
                hms = None
            fmt = lambda v: ("-" if v is None else f"{v:.1f}")
            print(f"  {name}: SW ok {S['ok_trials']}/{TR} (task-time {fmt(S['task_time_med'])}, coll {S['collisions']}, realloc {fmt(S['reallocations_med'])}, minbat {fmt(S['min_battery_med'])})"
                  f"  ||  Ours ok {O['ok_trials']}/{TR} (task-time {fmt(O['task_time_med'])}, coll {O['collisions']}, realloc {fmt(O['reallocations_med'])}, minbat {fmt(O['min_battery_med'])})"
                  f"  ||  head2head({len(both)}): task time {('+' + format(hms, '.0f') + '%') if hms is not None else 'n/a'}")
            results[f"agents_{n}"][name] = {"stopwait": S, "swaraj": O,
                                            "head2head_trials": len(both),
                                            "head2head_tasktime_pct": (round(hms, 1) if hms is not None else None)}
    json.dump(results, open("results.json", "w"), indent=2)
    print("\nsaved results.json")
