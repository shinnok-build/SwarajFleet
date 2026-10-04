# Contributing to SwarajFleet

Thanks for helping. The protocol is small on purpose — changes should stay that way.

## Ground rules

1. **Pure standard library.** No new dependencies without a strong, documented reason. The zero-dependency property is part of the design (edge hardware, offline warehouse LAN).
2. **Determinism is sacred.** Fixed seeds (`7 + trial`) reproduce everything. If your change alters seeded behavior, the committed `proto/results.json` and [docs/BENCHMARK-REPORT.md](docs/BENCHMARK-REPORT.md) must be regenerated and the diff explained in the PR.
3. **Claims need evidence.** Every number in docs/README must trace to a measurement or a source. No "improves performance" without the run.
4. **The A/B harness is the way to test protocol ideas.** Add a mode switch (see `SWARAJ_RO` in `proto/swaraj.py`), run the full benchmark on both, and show the table in the PR — then let the numbers decide.

## Setup

```bash
python3 --version          # 3.10+
python3 tests/test_smoke.py
```

That's it. Nothing to install.

## Running things

| What | Command |
|---|---|
| Smoke test (invariants) | `python3 tests/test_smoke.py` |
| Full benchmark (180 runs) | `python3 proto/swaraj.py` |
| Live demo | `python3 demo/server.py` → http://localhost:8321 |

## PR checklist

- [ ] Smoke test passes (`make test`)
- [ ] If protocol changed: full benchmark re-run attached, `results.json` updated
- [ ] Docs updated where behavior changed (README / ARCHITECTURE / BENCHMARK-REPORT)
- [ ] No new dependencies
- [ ] Commit messages say *why*, not just *what*

## What's a good first contribution?

- The 20/50-robot stress runs (roadmap item 1 in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#6-known-limits--roadmap))
- Message delay/loss injection into the benchmark (roadmap item 2)
- Typo fixes in docs — genuinely useful, genuinely welcome
