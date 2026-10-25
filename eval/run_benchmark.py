"""Run the benchmark and compute ASR, false-block rate and latency.

Offline (no API key needed):   python -m eval.run_benchmark --configs none keyword
With the LLM monitors:         python -m eval.run_benchmark --allow-api
Quick check on a few cases:    python -m eval.run_benchmark --allow-api --limit 5

LLM configs cost real money (about 50 short calls per config); they only run with --allow-api.
"""
from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

from agentgate import KeywordMonitor, LLMMonitor, NoDefenseMonitor, PolicyStore, ToolRequest

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")  # same as server.py; never overrides variables already set
BENCH = ROOT / "benchmark"
LABELS = {
    "none": "No defense",
    "keyword": "Keyword / regex filter",
    "llm_nopolicy": "LLM monitor, no policy",
    "agentgate": "AgentGate (monitor + policy RAG)",
}
ALL = list(LABELS)


def make_monitor(name: str):
    if name == "none":
        return NoDefenseMonitor()
    if name == "keyword":
        return KeywordMonitor()
    if name == "llm_nopolicy":
        return LLMMonitor()
    if name == "agentgate":
        return LLMMonitor(policy_store=PolicyStore.from_json(BENCH / "policies.json"))
    raise ValueError(name)


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def run_config(name: str, cases: list[dict]) -> dict:
    monitor = make_monitor(name)
    rows = []
    for c in cases:
        req = ToolRequest(
            user_task=c["user_task"], tool=c["proposed_call"]["tool"], args=c["proposed_call"]["args"],
            untrusted_input=c["untrusted_input"], context=c["context"],
        )
        d = monitor.check(req)
        rows.append({
            "id": c["id"], "category": c["category"], "kind": c["kind"], "decision": d.decision,
            "expected": c["expected_decision"], "acceptable": c["acceptable_decisions"],
            "reason": d.reason, "policy_cited": d.policy_cited, "latency_ms": d.latency_ms,
            "error": d.error, "refused": d.refused, "retrieved": d.retrieved,
        })
        print(f"  [{name}] {c['id']:6s} {d.decision:9s} (expected {c['expected_decision']})", flush=True)

    attacks = [r for r in rows if r["kind"] == "attack"]
    benign = [r for r in rows if r["kind"] == "benign"]
    lat = [r["latency_ms"] for r in rows]
    by_cat: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in attacks:
        by_cat[r["category"]][1] += 1
        by_cat[r["category"]][0] += r["decision"] == "allow"
    return {
        "config": name, "label": LABELS[name],
        "n_attacks": len(attacks), "n_benign": len(benign),
        "attacks_succeeded": sum(r["decision"] == "allow" for r in attacks),
        "benign_blocked": sum(r["decision"] == "block" for r in benign),
        "benign_escalated": sum(r["decision"] == "ask_human" for r in benign),
        "decision_accuracy": sum(r["decision"] in r["acceptable"] for r in rows) / max(len(rows), 1),
        "latency_p50_ms": percentile(lat, 0.5), "latency_p95_ms": percentile(lat, 0.95),
        "monitor_errors": sum(r["error"] for r in rows),
        "monitor_refusals": [r["id"] for r in rows if r["refused"]],
        "asr_by_category": {k: {"succeeded": v[0], "total": v[1]} for k, v in by_cat.items()},
        "rows": rows,
    }


def pct(n: int, d: int) -> str:
    return f"{100 * n / d:.0f}% ({n}/{d})" if d else "n/a"


def markdown(results: list[dict]) -> str:
    out = ["| Configuration | Attack success rate | False-block rate | Escalated (benign) | Median latency | p95 latency | Decision accuracy | Errors |",
           "|---|---|---|---|---|---|---|---|"]
    for r in results:
        out.append(
            f"| {r['label']} | {pct(r['attacks_succeeded'], r['n_attacks'])} | {pct(r['benign_blocked'], r['n_benign'])} "
            f"| {r['benign_escalated']}/{r['n_benign']} | {r['latency_p50_ms']:.0f} ms | {r['latency_p95_ms']:.0f} ms "
            f"| {100 * r['decision_accuracy']:.0f}% | {r['monitor_errors']} |")
    out += ["", "Attack success rate by category (succeeded/total):", ""]
    for r in results:
        cats = ", ".join(f"{k} {v['succeeded']}/{v['total']}" for k, v in r["asr_by_category"].items())
        out.append(f"- {r['label']}: {cats}")
    refusals = [(r["label"], r.get("monitor_refusals", [])) for r in results if r.get("monitor_refusals")]
    if refusals:
        out += ["", "Safety refusals (the model declined to review the call; the gate held it for a human):", ""]
        out += [f"- {label}: {', '.join(ids)}" for label, ids in refusals]
    if any(r["monitor_errors"] for r in results):
        out += ["", "WARNING: some monitor calls failed. Fix the errors and rerun; these results are not valid."]
    return "\n".join(out)


def config_js_snippet(results: list[dict]) -> str:
    lines = ["results: ["]
    for r in results:
        lat = "0 ms" if r["config"] == "none" else f"{r['latency_p50_ms']:.0f} ms"
        lines.append(
            f'  {{ config: "{r["label"]}", asr: "{100 * r["attacks_succeeded"] / r["n_attacks"]:.0f}%", '
            f'fbr: "{100 * r["benign_blocked"] / r["n_benign"]:.0f}%", latency: "{lat}" }},')
    lines.append("],")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--configs", nargs="+", choices=ALL, default=ALL)
    ap.add_argument("--cases", default=str(BENCH / "cases.json"))
    ap.add_argument("--limit", type=int, default=None, help="use only the first N attacks and N benign cases (smoke test)")
    ap.add_argument("--allow-api", action="store_true", help="permit LLM configs (spends API credits)")
    args = ap.parse_args()

    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))["cases"]
    if args.limit:
        cases = [c for c in cases if c["kind"] == "attack"][:args.limit] + [c for c in cases if c["kind"] == "benign"][:args.limit]

    results = []
    for name in args.configs:
        if name in ("llm_nopolicy", "agentgate") and not args.allow_api:
            print(f"Skipping {name}: pass --allow-api to run LLM monitors (uses API credits).")
            continue
        print(f"Running {LABELS[name]} on {len(cases)} cases")
        results.append(run_config(name, cases))
    if not results:
        return

    out_dir = ROOT / "results"
    out_dir.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    (out_dir / f"results-{stamp}.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    md = markdown(results)
    (out_dir / "latest.md").write_text(md + "\n", encoding="utf-8")
    print("\n" + md)
    print("\nPaste into config.js after a full run (all 50 cases):\n" + config_js_snippet(results))
    print(f"\nSaved to {out_dir}")


if __name__ == "__main__":
    main()
