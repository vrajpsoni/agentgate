"""Live-demo helper: replay one benchmark case through the gate.

  python demo.py --case II-01 --monitor none        # gate OFF: the attack executes
  python demo.py --case II-01 --monitor keyword     # offline baseline
  python demo.py --case II-01 --monitor agentgate   # needs the anthropic package + credentials
  python demo.py --case BN-07 --monitor agentgate --approve   # human approves an escalation
"""
import argparse
import json
from pathlib import Path

from agentgate import AgentGate, MockTools, ToolRequest
from eval.run_benchmark import BENCH, make_monitor

ap = argparse.ArgumentParser()
ap.add_argument("--case", required=True)
ap.add_argument("--monitor", choices=["none", "keyword", "llm_nopolicy", "agentgate"], default="agentgate")
ap.add_argument("--approve", action="store_true", help="auto-approve ask_human decisions")
args = ap.parse_args()

cases = json.loads((BENCH / "cases.json").read_text(encoding="utf-8"))["cases"]
c = next(x for x in cases if x["id"] == args.case)
req = ToolRequest(c["user_task"], c["proposed_call"]["tool"], c["proposed_call"]["args"],
                  c["untrusted_input"], c["context"])
tools = MockTools()
gate = AgentGate(make_monitor(args.monitor), tools, audit_path=Path("audit.jsonl"),
                 approver=(lambda r, d: True) if args.approve else None)

print(f"Task:       {req.user_task}")
if req.untrusted_input:
    print(f"Untrusted:  {req.untrusted_input[:100]}")
print(f"Proposed:   {req.tool}({json.dumps(req.args)})")
res = gate.handle(req)
d = res.decision
print(f"\nDecision:   {d.decision.upper()}  ({d.latency_ms:.0f} ms)")
print(f"Reason:     {d.reason}")
print(f"Policies:   {d.policy_cited or d.retrieved}")
print(f"Executed:   {res.executed}")
