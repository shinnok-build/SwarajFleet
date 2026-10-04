#!/usr/bin/env python3
"""Generate Python-canonical world specs (same seeds as harness) to JSON so the
JS port can run the IDENTICAL worlds and be compared tick-for-tick."""
import json, random, os
from sim import Map, LAYOUTS, setup_trial_v2

out = []
for n in (5, 10):
    for name, rows in LAYOUTS.items():
        m = Map(rows)
        for tr in range(12):
            rng = random.Random(7 + tr)
            homes, tasks, blocks = setup_trial_v2(m, n, rng, name=name)
            out.append({
                "layout": name, "n": n, "tr": tr,
                "homes": [list(h) for h in homes],
                "tasks": [[list(t.pickup), list(t.drop)] for t in tasks],
                "blocks": [{"cell": list(b.cell), "t0": b.t_start, "t1": b.t_end} for b in blocks],
            })
with open(os.path.join(os.path.dirname(__file__), "python_worlds.json"), "w") as f:
    json.dump(out, f)
print(f"wrote {len(out)} worlds")
