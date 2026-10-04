#!/usr/bin/env python3
"""D-layout cross-check fixtures: Python-canonical scale worlds + protocol results.

Worlds use the SAME generator and seeds (7+tr) as bench_scale.py trials, so the
browser cross-check replays actual locked benchmark trials bit-for-bit.
Cap 16000, same as the scale benchmark. Output goes to the repo fixtures dir
(python_worlds_D.json / python_results_D.json); crosscheck.js concatenates them
with the 72 locked A/B/C fixtures (which are NEVER regenerated here).
Run:  python3 gen_worlds_D.py [fixtures-dir]  (default: repo tests/site/fixtures)
"""
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(__file__))
import bench_scale  # noqa: F401  (registers layout D + AISLES["D"])
from sim import LAYOUTS, Map, Agent, Task, BlockEvent, setup_trial_v2
from swaraj import run_swaraj_v2

FIX = (sys.argv[1] if len(sys.argv) > 1
       else os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         os.pardir, "tests", "site", "fixtures"))
CAP = 16000

worlds, results = [], []
for n in (10, 20):
    m = Map(LAYOUTS["D"])
    for tr in range(6):
        rng = random.Random(7 + tr)
        homes, tasks, blocks = setup_trial_v2(m, n, rng, name="D")
        worlds.append({
            "layout": "D", "n": n, "tr": tr,
            "homes": [list(h) for h in homes],
            "tasks": [[list(t.pickup), list(t.drop)] for t in tasks],
            "blocks": [{"cell": list(b.cell), "t0": b.t_start, "t1": b.t_end} for b in blocks],
        })
        ag = [Agent(i, h, h, home=h) for i, h in enumerate(homes)]
        r = run_swaraj_v2(m, ag, tasks, blocks, name="D", cap=CAP)
        results.append({
            "layout": "D", "n": n, "tr": tr,
            "task_time": r["task_time"], "completed": r["completed"], "n_tasks": r["n_tasks"],
            "collisions": r["collisions"], "reallocations": r["reallocations"],
            "min_battery": r["min_battery"], "failed": r["failed"],
            "yield_events": r["yield_events"],
        })
        print(f"  D n={n} tr={tr}: done={r['completed']}/{n} tt={r['task_time']} "
              f"coll={r['collisions']} fail={r['failed']}", flush=True)

json.dump(worlds, open(os.path.join(FIX, "python_worlds_D.json"), "w"))
json.dump(results, open(os.path.join(FIX, "python_results_D.json"), "w"), indent=1)
print(f"wrote {len(worlds)} D worlds + results to {FIX}")
