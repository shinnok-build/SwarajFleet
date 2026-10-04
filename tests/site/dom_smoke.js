// DOM-level smoke test: boot the page with a fake DOM + canvas, pump frames,
// exercise every control. Catches runtime errors a syntax check can't.
"use strict";
const fs = require("fs");
const html = fs.readFileSync(require("path").join(__dirname, "..", "..", "site", "index.html"), "utf8");
const eng = html.match(/<script id="engine">([\s\S]*?)<\/script>/)[1];
const ui = html.match(/<script id="ui">([\s\S]*?)<\/script>/)[1];

function make2D() {
  const noop = () => {};
  return new Proxy({}, {
    get(t, p) { if (p in t) return t[p]; return (typeof p === "string") ? noop : undefined; },
    set(t, p, v) { t[p] = v; return true; },
  });
}
function makeCanvas() {
  return { width: 760, height: 430, style: {}, getContext: () => make2D(),
           parentElement: { clientWidth: 700 }, classList: { add: () => {}, remove: () => {} } };
}
function makeEl(id) {
  return {
    id, innerHTML: "", textContent: "", value: "", className: "",
    classList: { add: () => {}, remove: () => {}, toggle: () => {} },
    style: {}, children: [],
    appendChild(c) { this.children.push(c); }, prepend(c) { this.children.unshift(c); },
    removeChild() {}, addEventListener() {},
    onclick: null, onchange: null,
  };
}
const els = {};
const documentStub = {
  getElementById(id) {
    if (!els[id]) els[id] = (id === "cv-a" || id === "cv-b") ? makeCanvas() : makeEl(id);
    return els[id];
  },
  createElement(tag) { return makeEl(tag); },
  addEventListener() {},
  body: {},
};
const listeners = {};
const windowStub = { devicePixelRatio: 2, addEventListener(t, f) { (listeners[t] ||= []).push(f); } };
let rafQ = [];
global.window = windowStub;
global.document = documentStub;
global.getComputedStyle = () => ({ fontFamily: "sans-serif" });
global.requestAnimationFrame = (f) => { rafQ.push(f); return rafQ.length; };
global.location = { search: "", hostname: "team-null-pointer.github.io", pathname: "/swarajfleet/" };
global.addEventListener = () => {};

// Both <script> blocks share one global scope in a real browser; emulate by
// evaluating them as a single script. The engine's "use strict" is stripped
// so top-level class/function bindings land in the shared global scope.
(0, eval)(eng.replace(/^\s*"use strict";/, "") + "\n;window.__engineLoaded = true;\n" + ui);

// pump frames
let t = 0;
function pump(ms) {
  const end = t + ms;
  while (t < end && rafQ.length) {
    const f = rafQ.shift();
    t += 16;
    f(t);
  }
}
pump(3000); // ~3s of frames at 16ms

// exercise controls
const ids = ["btn-play", "btn-step", "sel-speed", "sel-layout", "sel-fleet", "inp-seed", "btn-reseed", "btn-reset", "btn-deadzone", "btn-kill", "btn-restore"];
for (const id of ids) {
  const el = documentStub.getElementById(id);
  if (el && el.onclick) el.onclick();
  if (el && el.onchange) el.onchange({ target: { value: id === "sel-speed" ? "0.1" : id === "sel-layout" ? "B" : id === "sel-fleet" ? "5" : "42" } });
}
pump(2000);
console.log("DOM SMOKE: boot + 5s of frames + all controls OK");
console.log("  status pill:", documentStub.getElementById("pill-txt").textContent);
console.log("  world pill:", documentStub.getElementById("pill-world").textContent);
console.log("  engine pill:", documentStub.getElementById("pill-engine").textContent);
console.log("  h2h:", documentStub.getElementById("h2h").innerHTML.slice(0, 80));
console.log("  tick-a:", documentStub.getElementById("k-tick-a").textContent, "tick-b:", documentStub.getElementById("k-tick-b").textContent);
console.log("  st-a:", documentStub.getElementById("st-a").textContent, "st-b:", documentStub.getElementById("st-b").textContent);
const repoLink = documentStub.getElementById("repo-link").innerHTML;
const repoOk = repoLink.includes("https://github.com/team-null-pointer/swarajfleet");
console.log("  repo link:", repoLink || "(empty)");
if (!repoOk) { console.error("FAIL: source link not auto-derived from Pages URL"); process.exit(1); }
