"""Local web demo for AgentGate. Standard library only; binds to 127.0.0.1.

  python server.py                 # opens http://127.0.0.1:8765
  python server.py --port 9000 --no-browser

Gate modes:
  replay   uses real decisions saved by record_replay.py (safest for a live demo)
  live     calls the Claude API (needs the anthropic package and ANTHROPIC_API_KEY)
  keyword  offline regex baseline (always available)
"""
import argparse
import json
import os
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from dotenv import load_dotenv

from agentgate import (AgentGate, Decision, KeywordMonitor, LLMMonitor, MockTools, NoDefenseMonitor, PolicyStore,
                       ToolRequest, WorkerAgent)

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
CASES = json.loads((ROOT / "benchmark" / "cases.json").read_text(encoding="utf-8"))["cases"]
BY_ID = {c["id"]: c for c in CASES}
SCENARIOS = json.loads((ROOT / "benchmark" / "agent_scenarios.json").read_text(encoding="utf-8"))["scenarios"]
SCENARIO_BY_ID = {s["id"]: s for s in SCENARIOS}
REPLAY_PATH = ROOT / "replay.json"
AUDIT_PATH = ROOT / "audit.jsonl"

TOOLS = MockTools()
LOCK = threading.Lock()
PENDING: dict[str, ToolRequest] = {}
AUDIT: list[dict] = []
_live_monitor = None


class ReplayMonitor:
    name = "replay"

    def __init__(self, data: dict) -> None:
        self.decisions = data["decisions"]
        self.model = data.get("model", "")
        self.current_case = None

    def check(self, req: ToolRequest) -> Decision:
        d = self.decisions[self.current_case]
        return Decision(d["decision"], d["reason"], d.get("policy_cited", []), d.get("confidence"),
                        d.get("latency_ms", 0.0), False, d.get("retrieved", []))


def load_replay():
    if REPLAY_PATH.exists():
        return ReplayMonitor(json.loads(REPLAY_PATH.read_text(encoding="utf-8")))
    return None


REPLAY = load_replay()


def live_available() -> bool:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401
        return True
    except ImportError:
        return False


def get_live():
    global _live_monitor
    if _live_monitor is None:
        _live_monitor = LLMMonitor(policy_store=PolicyStore.from_json(ROOT / "benchmark" / "policies.json"))
    return _live_monitor


def status() -> dict:
    return {
        "replay": REPLAY is not None,
        "live": live_available(),
        "keyword": True,
        "model": os.environ.get("AGENTGATE_MODEL", "claude-haiku-5-5"),
        "replay_model": REPLAY.model if REPLAY else None,
    }


def public_case(c: dict) -> dict:
    return {k: c[k] for k in ("id", "category", "kind", "description", "user_task", "untrusted_input",
                              "proposed_call", "expected_decision")}


def record(entry: dict) -> None:
    AUDIT.append(entry)
    with AUDIT_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def run_case(case_id: str, gate: str, mode: str) -> dict:
    c = BY_ID.get(case_id)
    if not c:
        raise ValueError(f"unknown case {case_id}")
    req = ToolRequest(c["user_task"], c["proposed_call"]["tool"], c["proposed_call"]["args"],
                      c["untrusted_input"], c["context"])
    if gate == "off":
        monitor, used = NoDefenseMonitor(), "off"
    elif mode == "replay":
        if not REPLAY:
            raise ValueError("replay.json not found. Run: python record_replay.py")
        REPLAY.current_case = case_id
        monitor, used = REPLAY, "replay"
    elif mode == "live":
        if not live_available():
            raise ValueError("live mode needs the anthropic package and ANTHROPIC_API_KEY in the server's environment")
        monitor, used = get_live(), "live"
    else:
        monitor, used = KeywordMonitor(), "keyword"

    with LOCK:
        res = AgentGate(monitor, TOOLS).handle(req)
        run_id = uuid.uuid4().hex[:8]
        d = res.decision
        needs_human = d.decision == "ask_human" and not res.executed
        if needs_human:
            PENDING[run_id] = req
        entry = {"run_id": run_id, "ts": time.strftime("%H:%M:%S"), "case": case_id, "gate": used,
                 "tool": req.tool, "decision": d.decision, "executed": res.executed,
                 "reason": d.reason, "latency_ms": round(d.latency_ms, 1),
                 "human": "pending" if needs_human else None}
        record(entry)
    return {"run_id": run_id, "gate": used, "decision": d.to_dict(), "executed": res.executed,
            "tool_result": res.tool_result, "needs_human": needs_human, "expected": c["expected_decision"],
            "error": d.error}


def run_agent(scenario_id: str, gate: str, mode: str) -> dict:
    """Run the live worker agent on a scenario; every tool call it makes goes through the gate."""
    s = SCENARIO_BY_ID.get(scenario_id)
    if not s:
        raise ValueError(f"unknown scenario {scenario_id}")
    if not live_available():
        raise ValueError("agent mode needs the anthropic package and ANTHROPIC_API_KEY in the server's environment")
    if gate == "off":
        monitor, used = NoDefenseMonitor(), "off"
    elif mode == "live":
        monitor, used = get_live(), "live"
    else:
        monitor, used = KeywordMonitor(), "keyword"

    agent_run = WorkerAgent(AgentGate(monitor, MockTools(files=s["files"]))).run(s["task"], s["context"])
    steps = []
    with LOCK:
        for step in agent_run.steps:
            run_id = uuid.uuid4().hex[:8]
            if step.needs_human:
                PENDING[run_id] = step.request
            d = step.decision
            record({"run_id": run_id, "ts": time.strftime("%H:%M:%S"), "case": scenario_id, "gate": f"agent/{used}",
                    "tool": step.tool, "decision": d["decision"], "executed": step.executed,
                    "reason": d["reason"], "latency_ms": round(d["latency_ms"], 1),
                    "human": "pending" if step.needs_human else None})
            steps.append({"run_id": run_id, **step.to_dict()})
    return {"gate": used, "steps": steps, "final_text": agent_run.final_text, "turns": agent_run.turns,
            "error": agent_run.error}


def approve(run_id: str, ok: bool) -> dict:
    with LOCK:
        req = PENDING.pop(run_id, None)
        if req is None:
            raise ValueError("no pending approval with that id")
        result = TOOLS.execute(req.tool, req.args) if ok else None
        record({"run_id": run_id, "ts": time.strftime("%H:%M:%S"), "case": "-", "gate": "human",
                "tool": req.tool, "decision": "approved" if ok else "rejected", "executed": ok,
                "reason": "human decision", "latency_ms": 0, "human": "approved" if ok else "rejected"})
    return {"executed": ok, "tool_result": result}


def results_markdown() -> str:
    p = ROOT / "results" / "latest.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # keep the console quiet
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            self._send(200, (ROOT / "web" / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/status":
            self._json(status())
        elif path == "/api/cases":
            self._json([public_case(c) for c in CASES])
        elif path == "/api/scenarios":
            self._json(SCENARIOS)
        elif path == "/api/audit":
            self._json(AUDIT[-100:])
        elif path == "/api/results":
            self._json({"markdown": results_markdown()})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            if self.path == "/api/run":
                self._json(run_case(body["case_id"], body.get("gate", "on"), body.get("mode", "replay")))
            elif self.path == "/api/agent":
                self._json(run_agent(body["scenario_id"], body.get("gate", "on"), body.get("mode", "live")))
            elif self.path == "/api/approve":
                self._json(approve(body["run_id"], bool(body["approve"])))
            elif self.path == "/api/reset":
                with LOCK:
                    AUDIT.clear()
                    PENDING.clear()
                    TOOLS.executed.clear()
                self._json({"ok": True})
            else:
                self._json({"error": "not found"}, 404)
        except (ValueError, KeyError) as e:
            self._json({"error": str(e)}, 400)
        except Exception as e:  # never crash the demo
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)  # localhost only
    url = f"http://127.0.0.1:{args.port}"
    s = status()
    print(f"AgentGate demo at {url}")
    print(f"  replay: {'ready' if s['replay'] else 'missing (run record_replay.py)'} | "
          f"live: {'ready' if s['live'] else 'unavailable (set ANTHROPIC_API_KEY, pip install anthropic)'} | keyword: ready")
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
