#!/usr/bin/env python3
"""Run the Python protocol on the exact worlds in python_worlds.json; save metrics."""
import json, os, random, sys
sys.path.insert(0, os.path.dirname(__file__))
from sim import Map, LAYOUTS, Agent, battery_tick, task_tick, block_active, run_baseline_v2
from swaraj import run_swaraj_v2

worlds = json.load(open(os.path.join(os.path.dirname(__file__), "python_worlds.json")))
results = []
for w in worlds:
    m = Map(LAYOUTS[w["layout"]])
    homes = [tuple(h) for h in w["homes"]]
    from sim import Task, BlockEvent
    tasks = [Task(i, tuple(pu), tuple(dr)) for i, (pu, dr) in enumerate(w["tasks"])]
    blocks = [BlockEvent(tuple(b["cell"]), b["t0"], b["t1"]) for b in w["blocks"]]
    ag = [Agent(i, h, h, home=h) for i, h in enumerate(homes)]
    r = run_swaraj_v2(m, ag, tasks, blocks, name=w["layout"])
    results.append({
        "layout": w["layout"], "n": w["n"], "tr": w["tr"],
        "task_time": r["task_time"], "completed": r["completed"], "n_tasks": r["n_tasks"],
        "collisions": r["collisions"], "reallocations": r["reallocations"],
        "min_battery": r["min_battery"], "failed": r["failed"],
        "yield_events": r["yield_events"],
    })
json.dump(results, open(os.path.join(os.path.dirname(__file__), "python_results.json"), "w"), indent=1)
fails = [r for r in results if r["failed"]]
print(f"{len(results)} runs, {len(fails)} failed")
for f in fails:
    print("  FAIL", f)
