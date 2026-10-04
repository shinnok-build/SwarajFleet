# proto/ — simulator + protocol (the project core)

| File | What it is |
|---|---|
| `sim.py` | World generation (3 warehouse layouts, 1-cell aisles, yield pockets), the **shared deterministic task layer** (claims/releases/dock courtesy/battery), the **faithful stop-and-wait baseline** (full view, static priority, same safety rules), and the independent collision detector |
| `swaraj.py` | **The SwarajFleet protocol** — single module, ~400 lines — plus the v2 task-world runner and the benchmark harness |
| `results.json` | Committed output of the last full benchmark (deterministic — regenerable at any time) |

## Run the benchmark

```bash
python3 swaraj.py
```

- 3 layouts × 30 fixed seeds (`7 + trial`) × {5, 10} robots, both systems
- Deterministic: same seeds, same machine-independent pure-Python logic
- Writes `results.json` next to this file; full analysis: [../docs/BENCHMARK-REPORT.md](../docs/BENCHMARK-REPORT.md)

## A/B protocol variants

```bash
SWARAJ_RO=rot  python3 swaraj.py    # default — the shipping protocol
SWARAJ_RO=dist python3 swaraj.py    # goal-distance right-of-way variant (A/B harness;
                                    # rejected in the full 180-run comparison — see
                                    # ../docs/ARCHITECTURE.md §5)
```

The switch exists so that protocol changes are always decided by the same-seed
comparison, never by intuition.
