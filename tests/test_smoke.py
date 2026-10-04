#!/usr/bin/env python3
"""SwarajFleet smoke test — the three invariants that matter.

Run:   python3 tests/test_smoke.py        (pure standard library; no pytest needed)
CI:    .github/workflows/ci.yml runs this on every push and PR.

Asserts on real protocol runs:
  1. zero collisions        (independent detector, outside the protocol)
  2. all tasks completed    (no deadlock before the cap)
  3. determinism            (same seed run twice => identical task time)
"""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "proto"))

from sim import LAYOUTS, Map, Agent, setup_trial_v2  # noqa: E402
import swaraj                                          # noqa: E402


def run_once(layout: str, n: int, seed: int) -> dict:
    m = Map(LAYOUTS[layout])
    rng = random.Random(seed)
    homes, tasks, blocks = setup_trial_v2(m, n, rng, name=layout)
    agents = [Agent(i, h, h, home=h) for i, h in enumerate(homes)]
    return swaraj.run_swaraj_v2(m, agents, tasks, blocks, name=layout)


def main() -> None:
    r10 = run_once("A", 10, 7)
    assert r10["collisions"] == 0, f"INVARIANT BROKEN: collisions={r10['collisions']}"
    assert not r10["failed"], f"INVARIANT BROKEN: run failed (deadlock?) at cap"
    assert r10["completed"] == r10["n_tasks"], "not all tasks completed"

    r10b = run_once("A", 10, 7)
    assert r10b["task_time"] == r10["task_time"], "nondeterministic: same seed, different result"

    r5 = run_once("A", 5, 7)
    assert r5["collisions"] == 0, f"INVARIANT BROKEN @5: collisions={r5['collisions']}"

    # 4. Wi-Fi dead zone (PS background: dead-zone vulnerability) — one
    #    robot's broadcast frozen 30 ticks mid-run: still 0 collisions,
    #    still every task completed (bounded time penalty, counted stops).
    m = Map(LAYOUTS["A"])
    rng = random.Random(7)
    homes, tasks, blocks = setup_trial_v2(m, 10, rng, name="A")
    dz = swaraj.run_swaraj_v2(m, [Agent(i, h, h, home=h) for i, h in enumerate(homes)],
                              tasks, blocks, name="A", drop=(3, 15, 45))
    assert dz["collisions"] == 0, f"DEAD-ZONE INVARIANT BROKEN: collisions={dz['collisions']}"
    assert dz["completed"] == dz["n_tasks"], "dead-zone: not all tasks completed"

    # 5. Forced re-assignment (PS req. 3: "re-assigning pickup points") —
    #    pickup cell blocked for 40 ticks: the re-assignment path fires,
    #    the task is still completed, 0 collisions.
    from sim import BlockEvent  # noqa: E402
    m = Map(LAYOUTS["A"])
    rng = random.Random(10)
    homes, tasks, blocks = setup_trial_v2(m, 10, rng, name="A")
    blocks.append(BlockEvent(tasks[0].pickup, 2, 42))
    ra = swaraj.run_swaraj_v2(m, [Agent(i, h, h, home=h) for i, h in enumerate(homes)],
                              tasks, blocks, name="A")
    assert ra["collisions"] == 0, f"REASSIGN INVARIANT BROKEN: collisions={ra['collisions']}"
    assert ra["reallocations"] >= 1, "forced re-assignment did not fire"
    assert ra["completed"] == ra["n_tasks"], "forced re-assignment: task lost"

    print(f"SMOKE OK — 10 robots: task_time={r10['task_time']} ticks, "
          f"collisions=0, min_battery={r10['min_battery']}, deterministic")
    print(f"SMOKE OK —   5 robots: task_time={r5['task_time']} ticks, collisions=0")


if __name__ == "__main__":
    main()
