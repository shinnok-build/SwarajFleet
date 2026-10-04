PYTHON ?= python3
NODE ?= node

.PHONY: benchmark demo test site-test test-all

benchmark:      ## full 180-run benchmark (deterministic) -> proto/results.json
	$(PYTHON) proto/swaraj.py

demo:           ## live demo -> http://localhost:8321  (PORT=... python3 demo/server.py)
	$(PYTHON) demo/server.py

test:           ## invariant smoke test (collisions=0, completion, determinism)
	$(PYTHON) tests/test_smoke.py

site-test:      ## headless tests for the browser simulation (Node)
	$(NODE) tests/site/engine.test.js
	$(NODE) tests/site/crosscheck.js
	$(NODE) tests/site/dom_smoke.js
	$(NODE) tests/site/dom_run.js

test-all: test site-test

stress:         ## PS stress proofs (dead-zone + forced re-assignment) -> proto/results_stress.json
	$(PYTHON) proto/stress_runs.py

bench3:         ## PS-minimum 3-robot config (3 layouts x 30 seeds) -> proto/results3.json
	$(PYTHON) proto/bench3.py

bench-scale:    ## scale benchmark: 20/50 robots (layout D) + 20 on locked B -> proto/results_scale.json (~1h)
	$(PYTHON) proto/bench_scale.py all

verify:         ## re-run all three benchmarks, confirm bit-identical to committed JSON
	$(PYTHON) tests/verify_results.py
