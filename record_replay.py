"""Record real AgentGate decisions for the web demo's replay mode.

  python record_replay.py          (needs your API key in .env; see README "Setup")

Runs all 50 benchmark cases through the live monitor once (about 50 short calls) and saves
replay.json. Refuses to save if any call failed, so replay never contains fake decisions.
"""
import json
import sys
import time
from pathlib import Path

from eval.run_benchmark import BENCH, run_config

cases = json.loads((BENCH / "cases.json").read_text(encoding="utf-8"))["cases"]
result = run_config("agentgate", cases)
if result["monitor_errors"]:
    sys.exit(f"{result['monitor_errors']} monitor calls failed; not saving replay.json. Check your key and rerun.")

import os
decisions = {
    r["id"]: {k: r[k] for k in ("decision", "reason", "policy_cited", "latency_ms", "retrieved")}
    for r in result["rows"]
}
out = {"model": os.environ.get("AGENTGATE_MODEL", "claude-haiku-5-5"),
       "recorded_at": time.strftime("%Y-%m-%d %H:%M:%S"), "decisions": decisions}
Path("replay.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"Saved replay.json with {len(decisions)} decisions.")
