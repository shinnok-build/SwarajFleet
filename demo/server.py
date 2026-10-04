#!/usr/bin/env python3
"""SWARAJFLEET live demo server — single-channel streaming (root-cause design).

Why this architecture:
  * At most two canonical live runs exist (ours / baseline, layout A, seed 8).
    Each runs in a background thread, paced at 4 Hz, and KEEPS RUNNING with
    zero viewers connected (kill-the-dashboard).
  * Every browser holds exactly ONE long-lived SSE connection (/channel) for
    the whole page lifetime. Switching systems, switching modes and reruns
    are small POST /control messages — zero new long-lived connections, so
    proxy connection limits, delayed closes and slot exhaustion cannot take
    the demo offline.
  * On (re)subscription the channel replays that run's buffered ticks, then
    follows the live edge. Joining mid-run is reported so the client can pin
    to the live edge instead of rewinding.

    python3 server.py [port]   →   http://localhost:[port]/
"""
import sys, os, json, time, threading, random, select, uuid
from urllib.parse import urlsplit, parse_qs
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, "..", "proto"))
from sim import LAYOUTS, Map, Agent, setup_trial_v2, run_baseline_v2  # noqa: E402
from swaraj import run_swaraj_v2                                      # noqa: E402

TICK_MS = 250          # 4 ticks/s real-time pace
LAYOUT = "A"
SEED = 8
N = 10
BASELINE_CAP = 150     # live baseline run stops well after the permanent freeze
RERUN_COOLDOWN = 5.0   # seconds, anti-flap
RUN_STALE_AFTER = 240  # a finished run auto-restarts if resubscribed after this
ST = {"IDLE": 0, "HOME": 1, "TO_PICK": 2, "CARRY": 3}


# ---------------- runs ----------------
class Run(threading.Thread):
    """One real protocol run, paced in real time, independent of viewers."""

    def __init__(self, system):
        super().__init__(daemon=True)
        self.system = system
        self.lock = threading.Lock()
        self.done = False
        self.result = None
        self.rows = []
        self.started_at = time.time()
        m = Map(LAYOUTS[LAYOUT])
        rng = random.Random(SEED)
        homes, tasks, blocks = setup_trial_v2(m, N, rng, name=LAYOUT)
        self.world = {
            "name": LAYOUT, "layout": LAYOUTS[LAYOUT],
            "homes": [list(h) for h in homes],
            "tasks": [[list(tk.pickup), list(tk.drop)] for tk in tasks],
            "blocks": [[list(b.cell), b.t_start, b.t_end] for b in blocks],
        }
        self.agents = [Agent(i, h, h, home=h) for i, h in enumerate(homes)]
        self._m, self._tasks, self._blocks = m, tasks, blocks

    def _record(self, t, agents, tasks, pos_snap, cand, move, blocks, intent, goal):
        row = {"t": t, "r": [], "ts": [], "blk": []}
        for a in agents:
            if self.system == "ours":
                n1, n2 = cand[a.aid]
            else:
                n1 = n2 = move[a.aid]
            row["r"].append([a.aid, pos_snap[a.aid][0], pos_snap[a.aid][1],
                             move[a.aid][0], move[a.aid][1], ST[a.state_task],
                             a.wait, 1 if a.temp_top else 0, n1[0], n1[1], n2[0], n2[1],
                             round(a.battery, 1)])
        row["ts"] = [tk.state for tk in tasks]
        row["blk"] = [list(b.cell) for b in blocks if b.t_start <= t < b.t_end]
        with self.lock:
            self.rows.append(row)

    def run(self):
        count = {"n": 0}
        start = time.time()

        def paced(t, agents, tasks, pos_snap, cand, move, blocks, intent, goal):
            self._record(t, agents, tasks, pos_snap, cand, move, blocks, intent, goal)
            count["n"] += 1
            target = start + count["n"] * TICK_MS / 1000.0
            time.sleep(max(0.0, target - time.time()))

        if self.system == "ours":
            res = run_swaraj_v2(self._m, self.agents, self._tasks, self._blocks,
                                name=LAYOUT, record=paced)
        else:
            res = run_baseline_v2(self._m, self.agents, self._tasks, self._blocks,
                                  name=LAYOUT, cap=BASELINE_CAP, record=paced)
        keep = ("completed", "task_time", "failed", "yield_events", "max_wait",
                "collisions", "reallocations")
        self.result = {k: res[k] for k in keep if k in res}
        with self.lock:
            self.done = True


RUNS = {}
RUNS_LOCK = threading.Lock()
_last_rerun = {}


def ensure_run(system):
    with RUNS_LOCK:
        r = RUNS.get(system)
        if r is None or (r.done and time.time() - r.started_at > RUN_STALE_AFTER):
            r = Run(system)
            r.start()
            RUNS[system] = r
        return r


def restart_run(system):
    now = time.time()
    with RUNS_LOCK:
        if now - _last_rerun.get(system, 0) < RERUN_COOLDOWN:
            return False
        _last_rerun[system] = now
        r = Run(system)
        r.start()
        RUNS[system] = r
    return True


# ---------------- channels ----------------
CHANNELS = {}
CHANNELS_LOCK = threading.Lock()


class Channel:
    """One SSE pump per browser tab. Follows a set of runs, replays buffers."""

    def __init__(self, h):
        self.h = h
        self.cid = uuid.uuid4().hex[:12]
        self.lock = threading.Lock()
        self.systems = []
        self.idx = {}
        self.done_sent = {}
        self.resync_pending = False

    def set_systems(self, systems):
        systems = [s for s in systems if s in ("ours", "baseline")]
        for s in systems:
            ensure_run(s)
        with self.lock:
            if systems != self.systems:
                self.systems = systems
                self.idx = {s: 0 for s in systems}
                self.done_sent = {s: False for s in systems}
                self.resync_pending = True

    def request_rerun(self):
        systems = list(self.systems) or ["ours"]
        restarted = [s for s in systems if restart_run(s)]
        with self.lock:
            for s in restarted:
                self.idx[s] = 0
                self.done_sent[s] = False
            if restarted:
                self.resync_pending = True

    # -- pump ----------------------------------------------------------
    def emit(self, obj):
        self.h.wfile.write(b"data: " + json.dumps(obj).encode() + b"\n\n")
        self.h.wfile.flush()

    def start(self):
        try:
            self.emit({"meta": True, "cid": self.cid, "tick_ms": TICK_MS,
                       "layout": LAYOUT, "seed": SEED, "n": N})
        except OSError:
            return
        last_keep = time.time()
        try:
            while True:
                if self.h._client_gone():
                    break
                with self.lock:
                    systems = list(self.systems)
                    resync = self.resync_pending
                    self.resync_pending = False
                if resync:
                    ticks = {s: len(RUNS[s].rows) for s in systems if s in RUNS}
                    world = next((RUNS[s].world for s in systems if s in RUNS), None)
                    self.emit({"resync": True, "systems": systems, "ticks": ticks,
                               "world": world, "n": N})
                sent_any = False
                for s in systems:
                    r = RUNS.get(s)
                    if r is None:
                        continue
                    with r.lock:
                        new = r.rows[self.idx.get(s, 0):]
                        self.idx[s] = len(r.rows)
                        finished = r.done
                    for row in new:
                        self.emit({"system": s, "row": row})
                        sent_any = True
                    if finished and self.idx[s] >= len(r.rows) and not self.done_sent.get(s):
                        self.done_sent[s] = True
                        self.emit({"system": s, "done": True, "result": r.result,
                                   "ticks": len(r.rows)})
                if not sent_any and time.time() - last_keep > 15:
                    last_keep = time.time()
                    self.h.wfile.write(b": keep-alive\n\n")
                    self.h.wfile.flush()
                time.sleep(0.05)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with CHANNELS_LOCK:
                CHANNELS.pop(self.cid, None)


# ---------------- http ----------------
class Handler(BaseHTTPRequestHandler):
    server_version = "SWARAJFLEET/2.0"

    def log_message(self, *args):
        pass

    def _client_gone(self):
        try:
            r, _, _ = select.select([self.connection], [], [], 0)
            if not r:
                return False
            self.connection.setblocking(False)
            try:
                return self.connection.recv(1) == b""
            finally:
                self.connection.setblocking(True)
        except OSError:
            return True

    def _http(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlsplit(self.path)
        p = u.path
        if p == "/":
            self._http(200, open(os.path.join(BASE, "index.html"), "rb").read(),
                       "text/html; charset=utf-8")
        elif p == "/replay":
            self._http(200, open(os.path.join(BASE, "demo_replay.json"), "rb").read(),
                       "application/json")
        elif p == "/health":
            self._http(200, b'{"ok": true}', "application/json")
        elif p == "/status":
            with RUNS_LOCK:
                runs = {s: {"done": r.done, "ticks": len(r.rows),
                            "age_s": round(time.time() - r.started_at, 1),
                            "result": r.result} for s, r in RUNS.items()}
            with CHANNELS_LOCK:
                n_ch = len(CHANNELS)
            self._http(200, json.dumps({"runs": runs, "channels": n_ch}).encode(),
                       "application/json")
        elif p == "/channel":
            self._channel()
        elif p == "/stream":
            self._legacy_stream(parse_qs(u.query))
        else:
            self._http(404, b'{"error": "not found"}', "application/json")

    def _sse_emit(self, obj):
        self.wfile.write(b"data: " + json.dumps(obj).encode() + b"\n\n")
        self.wfile.flush()

    def _legacy_stream(self, q):
        """Old per-system SSE (pre-single-channel clients). Served from the
        same run registry in the old message format, kept open after done."""
        system = (q.get("system") or ["ours"])[0]
        if system not in ("ours", "baseline"):
            self._http(400, b'{"error": "bad params"}', "application/json")
            return
        ensure_run(system)
        r = RUNS[system]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Connection", "keep-alive")
        self.send_header("retry", "2000")
        self.end_headers()
        try:
            self._sse_emit({"meta": True, "world": r.world, "system": system,
                            "n": N, "seed": SEED, "tick_ms": TICK_MS})
            i = 0
            done_sent = False
            last_keep = time.time()
            while True:
                if self._client_gone():
                    break
                with r.lock:
                    new = r.rows[i:]
                    i = len(r.rows)
                    finished = r.done
                for row in new:
                    self._sse_emit(row)
                if finished and i >= len(r.rows) and not done_sent:
                    done_sent = True
                    self._sse_emit({"done": True, "result": r.result,
                                    "ticks": len(r.rows)})
                if done_sent and time.time() - last_keep > 15:
                    last_keep = time.time()
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                time.sleep(0.05)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _channel(self):
        ch = Channel(self)
        with CHANNELS_LOCK:
            CHANNELS[ch.cid] = ch
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Connection", "keep-alive")
        self.send_header("retry", "2000")
        self.end_headers()
        ch.start()   # blocks until the client disconnects

    def do_POST(self):
        if urlsplit(self.path).path != "/control":
            self._http(404, b"{}", "application/json")
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._http(400, b'{"error": "bad json"}', "application/json")
            return
        with CHANNELS_LOCK:
            ch = CHANNELS.get(body.get("cid", ""))
        if ch is None:
            self._http(404, b'{"error": "unknown channel"}', "application/json")
            return
        action = body.get("action")
        if action == "subscribe":
            ch.set_systems(body.get("systems", []))
        elif action == "rerun":
            ch.request_rerun()
        else:
            self._http(400, b'{"error": "bad action"}', "application/json")
            return
        self._http(200, b'{"ok": true}', "application/json")


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("PORT", 8321))
    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"SWARAJFLEET live demo  →  http://localhost:{port}/")
    print(f"layout {LAYOUT} · seed {SEED} · {N} robots · 4 Hz ticks · 60 fps client · single channel")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
