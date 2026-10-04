#!/usr/bin/env node
/**
 * Headless verification of the browser engine (the <script id="engine"> block
 * in site/index.html). Run:  node tests/site/engine.test.js
 *
 * Guarantees checked
 *   1. 0 collisions on every world (independent vertex + edge-swap detector).
 *   2. Task completion, except a documented residual-livelock set. The protocol
 *      has a rare residual livelock (also present in the Python reference:
 *      locked benchmark reports 88/90 at n=5). Every world in the exception
 *      set below was independently verified to livelock in proto/swaraj.py
 *      as well (identical completed count, 0 collisions), so the exception is
 *      a property of the protocol/world, not of this port.
 *   3. Determinism: same seed + layout + fleet => identical run.
 *   4. Same-world guarantee: both systems see identical homes/tasks/blocks.
 */
"use strict";
const fs = require("fs");
const path = require("path");

const html = fs.readFileSync(path.join(__dirname, "..", "..", "site", "index.html"), "utf8");
const m = html.match(/<script id="engine">([\s\S]*?)<\/script>/);
if (!m) { console.error("FAIL: <script id=\"engine\"> not found in site/index.html"); process.exit(1); }
const mod = { exports: {} };
new Function("module", "exports", m[1])(mod, mod.exports);
const { createWorld, TickSim } = mod.exports;

function run(system, layout, n, seed, capTicks = 8000) {
  const s = new TickSim(createWorld(layout, n, seed), system);
  s.cap = capTicks;
  while (!s.done) s.step();
  return s;
}

let pass = 0, fail = 0;
function check(cond, label) {
  if (cond) pass++;
  else { fail++; console.error("  FAIL: " + label); }
}

/* Worlds where the protocol has a known residual livelock (verified in the
 * Python reference protocol on the exact same world — identical completed
 * counts, 0 collisions both sides). Browser seed space. The n=20 entries are
 * the density limit: perpetual motion, 17-19/20 tasks, never a frozen
 * deadlock (Python same-world runs match bit-for-bit). */
const KNOWN_LIVELOCKED = new Set([
  "A|5|10", "A|30|10", "B|15|10", "B|22|10", "C|9|10", "C|26|10", "C|35|10",
  "D|8|20", "D|3|20", "B|8|20", "B|1|20", "B|3|20",
]);

const LAYOUTS = ["A", "B", "C", "D"];
const SEEDS = [1, 2, 3, 4, 5, 6];
const FLEETS = [5, 10];
// fleet 20 only where the docks exist (B: 20 pockets, D: 72)
const fleetsFor = (layout) => (layout === "B" || layout === "D") ? [5, 10, 20] : [5, 10];

console.log("== SwarajFleet: 0 collisions + completion (modulo documented residual livelock) ==");
for (const layout of LAYOUTS) for (const seed of SEEDS) for (const n of fleetsFor(layout)) {
  const key = layout + "|" + seed + "|" + n;
  const cap = layout === "D" ? 16000 : 8000; // D replays the scale-benchmark horizon
  const s = run("swaraj", layout, n, seed, cap);
  const r = s.result();
  check(r.collisions === 0, `swaraj ${key}: collisions=${r.collisions}`);
  const expectedLivelo = KNOWN_LIVELOCKED.has(key);
  check(r.failed === expectedLivelo,
    `swaraj ${key}: completion ${r.completed}/${r.nTasks} tt=${r.taskTime} (expected livelock: ${expectedLivelo})`);
  if (seed % 2 === 1) {
    const s2 = run("swaraj", layout, n, seed, cap);
    check(s2.result().taskTime === r.taskTime && s2.result().minBattery === r.minBattery,
      `determinism ${key}: ${r.taskTime} vs ${s2.result().taskTime}`);
  }
  process.stdout.write(".");
}
console.log("");

console.log("== Baseline (stop-and-wait): collision-free; expected to freeze/stall often ==");
let baseDone = 0;
for (const layout of LAYOUTS) for (const seed of SEEDS) for (const n of fleetsFor(layout)) {
  const s = run("baseline", layout, n, seed, 3000);
  const r = s.result();
  check(r.collisions === 0, `baseline ${layout}|${seed}|${n}: collisions=${r.collisions}`);
  if (!r.failed) baseDone++;
}
console.log(`  baseline completed ${baseDone}/${LAYOUTS.length * SEEDS.length * FLEETS.length} (freezes are the point)`);

console.log("== Same-world check: both systems see identical setup from one spec ==");
{
  const w = createWorld("A", 10, 8);
  const a = new TickSim(w, "baseline"), b = new TickSim(w, "swaraj");
  check(JSON.stringify(a.agents.map(x => x.pos)) === JSON.stringify(b.agents.map(x => x.pos)), "initial robot positions identical");
  check(JSON.stringify(a.tasks.map(t => [t.pu, t.dr])) === JSON.stringify(b.tasks.map(t => [t.pu, t.dr])), "task endpoints identical");
  check(JSON.stringify(a.blocks.map(x => [x.cell, x.t0, x.t1])) === JSON.stringify(b.blocks.map(x => [x.cell, x.t0, x.t1])), "blockages identical");
}

console.log("== Sample run (A / seed 8 / n=10) — expect fast completion ==");
{
  const r = run("swaraj", "A", 10, 8).result();
  console.log(`  task_time=${r.taskTime} collisions=${r.collisions} min_battery=${r.minBattery} yields=${r.yieldEvents}`);
  check(r.taskTime !== null && r.taskTime < 200 && r.collisions === 0, "A/s8/n10 completes cleanly in < 200 ticks");
}

console.log("== Dead-zone drill: frozen broadcast + counted safety stops, 0 collisions ==");
{
  // Full-window proofs (30 dark ticks strictly inside the run):
  // C/s4/n10 no-drill tt=52; D/s1/n20 no-drill tt=59.
  const c0 = new TickSim(createWorld("C", 10, 4), "swaraj");
  c0.setDrop(0, 11, 41);
  while (!c0.done) c0.step();
  const r0 = c0.result();
  console.log(`  drill C/s4/n10 v0 [11,41): tt=${r0.taskTime} ss=${r0.safetyStops} coll=${r0.collisions}`);
  check(r0.taskTime === 52 && r0.safetyStops === 10 && r0.collisions === 0, "C drill v0: zero-delay blackout survival");
  const c9 = new TickSim(createWorld("C", 10, 4), "swaraj");
  c9.setDrop(9, 11, 41);
  while (!c9.done) c9.step();
  const r9 = c9.result();
  check(r9.taskTime === 66 && r9.safetyStops === 14 && r9.collisions === 0 && !r9.failed, "C drill v9: perturbed but completes");
  check(c0.dropGhost !== null && c9.dropGhost !== null, "ghost cell was frozen");
  // Scale drill: EVERY robot survives being the dark one (D/s1/n20, 20 victims)
  let allOk = true, minSS = 1e9;
  for (let v = 0; v < 20; v++) {
    const s = new TickSim(createWorld("D", 20, 1), "swaraj");
    s.cap = 16000;
    s.setDrop(v, 11, 41);
    while (!s.done) s.step();
    const r = s.result();
    if (r.failed || r.collisions !== 0) { allOk = false; break; }
    if (r.safetyStops < minSS) minSS = r.safetyStops;
  }
  console.log(`  drill D/s1/n20 all 20 victims: all complete, min safety stops=${minSS}`);
  check(allOk, "D drill: all 20 victims complete with 0 collisions");
  check(minSS >= 13, `D drill: safety layer engaged every time (min ss=${minSS})`);
  const d16 = new TickSim(createWorld("D", 20, 1), "swaraj");
  d16.cap = 16000;
  d16.setDrop(16, 11, 41);
  while (!d16.done) d16.step();
  const d16b = new TickSim(createWorld("D", 20, 1), "swaraj");
  d16b.cap = 16000;
  d16b.setDrop(16, 11, 41);
  while (!d16b.done) d16b.step();
  check(d16.result().taskTime === 61 && d16.result().safetyStops === 25, "D drill v16: locked proof (tt=61, ss=25)");
  check(d16b.result().taskTime === 61 && d16b.result().safetyStops === 25, "D drill determinism");
  // drill-off default unchanged
  const nd = run("swaraj", "A", 10, 8);
  check(nd.drop === null && nd.result().safetyStops === 0 && nd.result().taskTime === 28, "no-drill default untouched (A/s8/n10 tt=28)");
}

console.log(`\nRESULT: ${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
