"""
Scale benchmark — the committed pre-finale runs: 20 and 50 robots.

Method is IDENTICAL to the locked 180-run benchmark (same trial generator,
same faithful full-view stop-and-wait baseline, same shared task controller,
independent collision detector, fixed seeds 7+tr, 30 trials per config):

  D:10 / D:20 / D:50  — the SCALE layout: a 21x35 warehouse (6 vertical x
                        7 horizontal aisles, 72 off-aisle pockets). Generated
                        deterministically in this file so the world is part of
                        the repo and reproducible byte-for-byte.
  B:20               — 20 robots on LOCKED layout B (20 pockets), i.e. the
                        densest possible fleet on an already-locked world.

Run:  python3 bench_scale.py all            (everything, ~45 min)
      python3 bench_scale.py D:50 B:20      (subset; merges into results_scale.json)
"""
import json
import os
import random
import statistics
import sys
import time

from sim import LAYOUTS, AISLES, Map, Agent, setup_trial_v2, run_baseline_v2
from swaraj import run_swaraj_v2

TR = 30

# ---------------------------------------------------------------- scale layout
V_D = [1, 7, 13, 19, 25, 31]            # vertical aisle columns
H_D = [1, 4, 7, 10, 13, 16, 19]         # horizontal aisle rows
POCKET_MIDS = [4, 10, 16, 22, 28, 33]   # one pocket per rack block
W_D, H_ROWS = 35, 21


def scale_layout():
    rows = []
    for y in range(H_ROWS):
        if y == 0 or y == H_ROWS - 1:
            rows.append("#" * W_D)
        elif y in H_D:
            rows.append("#" + "." * (W_D - 2) + "#")
        else:
            cells = ["#"] * W_D
            for x in range(1, W_D - 1):
                if x in V_D or x in POCKET_MIDS:
                    cells[x] = "."
            rows.append("".join(cells))
    return rows


LAYOUTS["D"] = scale_layout()
AISLES["D"] = (V_D, H_D)

CONFIGS = [("D", 10), ("D", 20), ("D", 50), ("B", 20)]


def run_config(name, n, out_path="results_scale.json"):
    m = Map(LAYOUTS[name])
    # Horizon scales with world size: the scale layout D is 21x35 with up to
    # 50 tasks, so its cap is 16000 ticks (both systems get the same cap).
    # Locked layout B keeps the locked benchmark's 8000-tick cap.
    cap = 16000 if name == "D" else 8000
    from sim import _pocket_cells
    print(f"--- {name}:{n}  (layout {name} {m.H}x{m.W}, pockets={len(_pocket_cells(m, name))}, cap={cap}) ---")
    sw, of = [], []
    t0 = time.time()
    for tr in range(TR):
        rng = random.Random(7 + tr)
        homes, tasks, blocks = setup_trial_v2(m, n, rng, name=name)
        sw.append(run_baseline_v2(m, [Agent(i, h, h, home=h) for i, h in enumerate(homes)],
                                  tasks, blocks, name=name, cap=cap))
        rng2 = random.Random(7 + tr)
        homes2, tasks2, blocks2 = setup_trial_v2(m, n, rng2, name=name)
        assert homes == homes2, "world determinism broken"
        of.append(run_swaraj_v2(m, [Agent(i, h, h, home=h) for i, h in enumerate(homes2)],
                                tasks2, blocks2, name=name, cap=cap))
        el = time.time() - t0
        print(f"  trial {tr + 1:2d}/{TR}  sw={sw[-1]['completed']}/{n} "
              f"swaraj={of[-1]['completed']}/{n}  ({el:.0f}s elapsed)")

    def stats(rs):
        ok = [r for r in rs if not r["failed"]]
        return dict(
            ok_trials=len(ok), trials=TR,
            task_time_med=(round(statistics.median([r["task_time"] for r in ok]), 1) if ok else None),
            task_time_mean=(round(statistics.mean([r["task_time"] for r in ok]), 1) if ok else None),
            collisions=sum(r["collisions"] for r in rs),
            deadlocks=sum(1 for r in rs if r["failed"]),
            completed_sum=sum(r["completed"] for r in rs),
        )
    S, O = stats(sw), stats(of)
    both = [(a, b) for a, b in zip(sw, of) if not a["failed"] and not b["failed"]]
    hms = None
    if both:
        hms = 100 * (statistics.median([a["task_time"] for a, _ in both])
                     - statistics.median([b["task_time"] for _, b in both])) \
            / statistics.median([a["task_time"] for a, _ in both])
    fmt = lambda v: ("-" if v is None else f"{v:.1f}")
    print(f"  {name}:{n}  SW ok {S['ok_trials']}/{TR} (t {fmt(S['task_time_med'])} med, "
          f"coll {S['collisions']}, sum {S['completed_sum']}/{TR * n})  ||  "
          f"Ours ok {O['ok_trials']}/{TR} (t {fmt(O['task_time_med'])} med, coll {O['collisions']}, "
          f"sum {O['completed_sum']}/{TR * n})  ||  head2head({len(both)}): "
          f"{('+' + format(hms, '.0f') + '%') if hms is not None else 'n/a'}")
    key = f"agents_{n}:{name}"
    results = {}
    if os.path.exists(out_path):
        results = json.load(open(out_path))
    results[key] = {"stopwait": S, "swaraj": O,
                    "head2head_trials": len(both),
                    "head2head_tasktime_pct": (round(hms, 1) if hms is not None else None)}
    json.dump(results, open(out_path, "w"), indent=2)
    print(f"  saved {key} -> {out_path}  ({time.time() - t0:.0f}s)")


def main():
    sel = sys.argv[1:] or ["all"]
    if sel == ["all"]:
        targets = CONFIGS
    else:
        targets = []
        for s in sel:
            nm, n = s.split(":")
            targets.append((nm, int(n)))
    for name, n in targets:
        run_config(name, n)


if __name__ == "__main__":
    main()
