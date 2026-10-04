// Full-run DOM test: boot, set max speed, pump until BOTH systems finish
// (complete / frozen / cap), then verify end-state rendering.
"use strict";
const fs = require("fs");
const path = require("path");
const html = fs.readFileSync(path.join(__dirname, "..", "..", "site", "index.html"), "utf8");
const eng = html.match(/<script id="engine">([\s\S]*?)<\/script>/)[1];
const ui = html.match(/<script id="ui">([\s\S]*?)<\/script>/)[1];

function make2D() {
  const noop = () => {};
  return new Proxy({}, { get(t, p) { return p in t ? t[p] : noop; }, set(t, p, v) { t[p] = v; return true; } });
}
function makeCanvas() {
  return { width: 760, height: 430, style: {}, getContext: () => make2D(),
           parentElement: { clientWidth: 700 }, classList: { add: () => {}, remove: () => {} } };
}
function makeEl(id) {
  return { id, innerHTML: "", textContent: "", value: "", className: "",
           classList: { add: () => {}, remove: () => {}, toggle: () => {} },
           style: {}, children: [], appendChild() {}, prepend() {}, removeChild() {},
           addEventListener() {}, onclick: null, onchange: null };
}
const els = {};
const documentStub = {
  getElementById(id) { if (!els[id]) els[id] = (id === "cv-a" || id === "cv-b") ? makeCanvas() : makeEl(id); return els[id]; },
  createElement() { return makeEl("tmp"); },
  addEventListener() {}, body: {},
};
let rafQ = [];
global.window = { devicePixelRatio: 2, addEventListener() {} };
global.document = documentStub;
global.getComputedStyle = () => ({ fontFamily: "sans-serif" });
global.requestAnimationFrame = (f) => { rafQ.push(f); return rafQ.length; };
global.location = { search: "" };
global.addEventListener = () => {};

(0, eval)(eng.replace(/^\s*"use strict";/, "") + ui);

const $ = (id) => documentStub.getElementById(id);
let t = 0;
function pump(ms) {
  const end = t + ms;
  while (t < end && rafQ.length) { const f = rafQ.shift(); t += 16; f(t); }
}
function bothFinished() {
  return ["a", "b"].every((k) => {
    const s = $("st-" + k).textContent;
    return s.startsWith("COMPLETE") || s.startsWith("FROZEN") || s.startsWith("STALLED");
  });
}

// default world: layout A, seed 8, n=10 — max speed
$("sel-speed").onchange({ target: { value: "0.1" } });
pump(16); // let play() resume with new speed
let guard = 0;
while (!bothFinished() && guard++ < 400000) pump(16);

const stA = $("st-a").textContent, stB = $("st-b").textContent;
const h2h = $("h2h").innerHTML;
console.log("swaraj:", stB, "| tick", $("k-tick-b").textContent, "| tasks", $("k-task-b").textContent,
            "| bat", $("k-bat-b").textContent, "| coll", $("k-coll-b").textContent, "| yields", $("k-yld-b").textContent);
console.log("baseline:", stA, "| tick", $("k-tick-a").textContent, "| tasks", $("k-task-a").textContent,
            "| bat", $("k-bat-a").textContent, "| coll", $("k-coll-a").textContent);
console.log("h2h:", h2h);
const c1 = !stB.startsWith("RUNNING"), c2 = !stA.startsWith("RUNNING");
// (fake-DOM textContent is not string-coerced like a real browser's)
const c3 = Number($("k-coll-b").textContent) === 0, c4 = Number($("k-coll-a").textContent) === 0;
const c5 = /earlier|froze|stalled/i.test(h2h);
const ok = c1 && c2 && c3 && c4 && c5;
console.log(ok ? "DOM FULL-RUN: OK" : "DOM FULL-RUN: UNEXPECTED STATE");
process.exit(ok ? 0 : 1);
