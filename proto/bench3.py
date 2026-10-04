"""
PS-minimum fleet config: 3 AMRs (the PS says "at least 3 AMRs").
Same method as the locked 180-run benchmark (3 layouts x 30 seeds, task
world, blocked intersections, faithful full-view baseline, independent
collision detector) at the PS's own minimum fleet size.
Deterministic: fixed seeds. Run: python3 bench3.py  ->  results3.json
"""
import json
import random
import statistics

from sim import LAYOUTS, Map, Agent, setup_trial_v2, run_baseline_v2
from swaraj import run_swaraj_v2

TR = 30


def main():
    results = {}
    print("===== 3 agents & 3 tasks, %d trials/layout (PS minimum 'at least 3 AMRs') =====" % TR)
    for name, rows in LAYOUTS.items():
        m = Map(rows)
        sw, of = [], []
        for tr in range(TR):
            rng = random.Random(7 + tr)
            homes, tasks, blocks = setup_trial_v2(m, 3, rng, name=name)
            sw.append(run_baseline_v2(m, [Agent(i, h, h, home=h) for i, h in enumerate(homes)],
                                      tasks, blocks, name=name))
            rng2 = random.Random(7 + tr)
            homes2, tasks2, blocks2 = setup_trial_v2(m, 3, rng2, name=name)
            assert homes == homes2
            of.append(run_swaraj_v2(m, [Agent(i, h, h, home=h) for i, h in enumerate(homes2)],
                                    tasks2, blocks2, name=name))

        def stats(rs):
            ok = [r for r in rs if not r["failed"]]
            return dict(
                ok_trials=len(ok), trials=TR,
                task_time_med=(statistics.median([r["task_time"] for r in ok]) if ok else None),
                collisions=sum(r["collisions"] for r in rs),
                deadlocks=sum(1 for r in rs if r["failed"]),
            )
        S, O = stats(sw), stats(of)
        both = [(a, b) for a, b in zip(sw, of) if not a["failed"] and not b["failed"]]
        hms = None
        if both:
            hms = 100 * (statistics.median([a["task_time"] for a, _ in both])
                         - statistics.median([b["task_time"] for _, b in both])) \
                / statistics.median([a["task_time"] for a, _ in both])
        fmt = lambda v: ("-" if v is None else f"{v:.1f}")
        print(f"  {name}: SW ok {S['ok_trials']}/{TR} (task-time {fmt(S['task_time_med'])}, "
              f"coll {S['collisions']})  ||  Ours ok {O['ok_trials']}/{TR} "
              f"(task-time {fmt(O['task_time_med'])}, coll {O['collisions']})  ||  "
              f"head2head({len(both)}): {('+' + format(hms, '.0f') + '%') if hms is not None else 'n/a'}")
        results[f"agents_3:{name}"] = {"stopwait": S, "swaraj": O,
                                       "head2head_trials": len(both),
                                       "head2head_tasktime_pct": (round(hms, 1) if hms is not None else None)}
    json.dump(results, open("results3.json", "w"), indent=2)
    print("\nsaved results3.json")


if __name__ == "__main__":
    main()
