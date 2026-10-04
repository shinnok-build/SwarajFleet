#!/usr/bin/env node
/**
 * Faithfulness cross-check: run the browser engine on the EXACT worlds the
 * Python reference protocol was benchmarked on, and assert the JS results
 * match the recorded Python results (task_time, completed, collisions,
 * reallocations, min_battery, failed) world for world.
 *
 * Fixtures are the raw output of proto/gen_worlds.py (world specs) and
 * proto/run_worlds.py (Python protocol results) — 72 worlds across
 * layouts A/B/C, fleets 5 and 10, 12 seeds each — PLUS the scale fixtures
 * from proto/gen_worlds_D.py, which replay locked bench_scale trials
 * (layout D, 16k horizon). Regenerate with:
 *   python3 proto/gen_worlds.py && python3 proto/run_worlds.py && python3 proto/gen_worlds_D.py
 *
 * Run:  node tests/site/crosscheck.js
 */
"use strict";
const fs = require("fs");
const path = require("path");

const html = fs.readFileSync(path.join(__dirname, "..", "..", "site", "index.html"), "utf8");
const m = html.match(/<script id="engine">([\s\S]*?)<\/script>/);
if (!m) { console.error("FAIL: <script id=\"engine\"> not found"); process.exit(1); }
const mod = { exports: {} };
new Function("module", "exports", m[1])(mod, mod.exports);
const { createWorldFromSpec, TickSim } = mod.exports;

const fx = (f) => path.join(__dirname, "fixtures", f);
const worlds = JSON.parse(fs.readFileSync(fx("python_worlds.json"), "utf8"))
  .concat(JSON.parse(fs.readFileSync(fx("python_worlds_D.json"), "utf8")));
const pyres = JSON.parse(fs.readFileSync(fx("python_results.json"), "utf8"))
  .concat(JSON.parse(fs.readFileSync(fx("python_results_D.json"), "utf8")));
if (worlds.length !== pyres.length) { console.error("FAIL: fixture length mismatch"); process.exit(1); }

let agree = 0, disagree = 0;
const dlines = [];
for (let i = 0; i < worlds.length; i++) {
  const w = worlds[i], pr = pyres[i];
  const s = new TickSim(createWorldFromSpec(w.layout, w.homes, w.tasks, w.blocks), "swaraj");
  s.cap = w.layout === "D" ? 16000 : 8000; // D replays scale-benchmark trials
  while (!s.done) s.step();
  const jr = s.result();
  const ok = jr.taskTime === pr.task_time && jr.completed === pr.completed &&
             jr.collisions === pr.collisions && jr.reallocations === pr.reallocations &&
             Math.round(jr.minBattery * 10) === Math.round(pr.min_battery * 10) &&
             jr.failed === pr.failed;
  if (ok) agree++;
  else {
    disagree++;
    dlines.push(`  ${w.layout}/n${w.n}/tr${w.tr}:  PY tt=${pr.task_time} done=${pr.completed} bat=${pr.min_battery} fail=${pr.failed}` +
                `   JS tt=${jr.taskTime} done=${jr.completed} bat=${jr.minBattery} fail=${jr.failed}`);
  }
}
console.log(`CROSS-CHECK (JS engine vs Python reference, ${worlds.length} identical worlds):\n  ${agree} agree, ${disagree} disagree`);
if (dlines.length) { console.error(dlines.join("\n")); process.exit(1); }
console.log("  OK — the browser engine reproduces the Python protocol bit-for-bit on every world.");
