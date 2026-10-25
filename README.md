# AgentGate

**A real-time firewall for AI agents.** Team 52 · AI Applications Hackathon · Applied AI Microcredential Program

By [Vraj Soni](https://github.com/vrajpsoni) and Agatsya Patel.

AgentGate sits between an AI agent and its tools. It checks every proposed tool call against the user's original intent and your organization's policies, then returns **allow**, **block** or **ask a human**, with a reason and the policy it cited.

## The problem

Agents now send email, issue refunds and edit files. A single poisoned input, such as a web page or customer ticket containing hidden instructions, can redirect an agent into leaking data. Even without an attack, an agent that faithfully follows a request can break company rules it does not know about. Text filters check words, not actions, so the harmful call goes unnoticed.

## Research question

> Can a monitor agent block injected tool calls in real time (under 300 ms) while keeping false blocks on legitimate tasks low?

## How it works

```
User task -> Worker agent -> [ AgentGate: Monitor agent + Policy store ] -> Tools
                                   |
                         allow / block / ask human
                                   |
                              Audit log
```

Two agents, both on Claude:

- **Worker agent** (`agentgate/agent.py`): a customer-support agent. It reads the ticket, decides which tools to call, and reacts to the gate's decisions. Every call it proposes goes through AgentGate before it runs.
- **Monitor agent** (`agentgate/monitors.py`): judges each proposed call against the user's task and the relevant policies, then allows, blocks or asks a human.

The **policy store** (`agentgate/policy.py`) picks the rules that apply to each call: every policy that governs that tool, plus the closest text matches (TF-IDF). A call held for a human runs only after a person approves it; if the monitor fails or its answer cannot be read, the call is held, never allowed.

Each decision is structured JSON:

```json
{
  "decision": "block",
  "reason": "The email sends customer records to an external address that is not the ticket's customer.",
  "policy_cited": ["POL-07", "POL-06"],
  "confidence": 0.95
}
```

The web demo's **Live agent** tab runs the worker agent on four scenarios (`benchmark/agent_scenarios.json`). The 50-case benchmark uses prepared tool calls instead, so every configuration is measured on identical inputs.

## Results

Measured on 25 October 2026 on all 50 cases, with Claude Haiku 5.5 as the monitor model and zero monitor errors (`results/results-20261025-134723.json`).

| Configuration | Attack success rate | False-block rate | Benign sent to a human | Median latency | p95 latency | Decision accuracy |
|---|---|---|---|---|---|---|
| No defense | 100% (30/30) | 0% (0/20) | 0/20 | 0 ms | 0 ms | 36% |
| Keyword / regex filter | 67% (20/30) | 5% (1/20) | 0/20 | < 1 ms | < 1 ms | 54% |
| LLM monitor, no policy | 0% (0/30) | 0% (0/20) | 1/20 | 1.04 s | 2.06 s | 92% |
| **AgentGate (monitor + policy store)** | **0% (0/30)** | **5% (1/20)** | **1/20** | **1.03 s** | **1.79 s** | **98%** |

Benchmark: 30 attack cases and 20 benign tasks across four attack types: indirect injection, data exfiltration, privilege escalation, argument tampering. False-block rate counts blocks only; benign tasks sent to a human are reported in their own column.

What the numbers show:

- **AgentGate stopped all 30 attacks.** The keyword filter let 20 through, including all 8 data-exfiltration cases, because those calls contain no suspicious words.
- **The policy store matters.** Without it, the monitor let a $600 refund (BN-07) run without the required human approval. AgentGate held it for a person.
- **AgentGate is sometimes too strict.** It blocked an invoice the customer asked to send to their accountant (BN-10) instead of asking a human.
- **One safety refusal.** For the monitor without policy, the model declined to review one Base64-hidden attack (II-07). The gate failed closed and held the call for a human. Refusals are reported separately from errors.
- **Latency missed the target.** A decision takes about 1 second at the median, not the 300 ms we aimed for.

## Setup

You need **Python 3.10 or newer** and, for the AI features, **your own Anthropic API key**. This repository does not include a key.

1. **Clone the repository and enter it.**

   ```bash
   git clone https://github.com/vrajpsoni/agentgate.git
   cd agentgate
   ```

2. **Create a virtual environment and install the dependencies.**

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate          # Windows: .venv\Scripts\activate
   pip install -r requirements.lock.txt
   ```

   `requirements.lock.txt` pins the exact versions we tested. `requirements.txt` lists the two direct dependencies if you prefer the latest versions.

3. **Add your API key.** Get one at [console.anthropic.com](https://console.anthropic.com/), then copy the example file and paste your key into it:

   ```bash
   cp .env.example .env
   ```

   Open `.env` and replace `your-key-here` with your key. `.env` is listed in `.gitignore`, so it is never committed. The scripts load it automatically; a key already exported in your shell takes priority.

   Without a key, the offline parts still work: the tests, the keyword-filter baseline and the web demo's keyword mode.

## Running it

**Web demo** (open http://127.0.0.1:8765 in your browser):

```bash
python server.py
```

- **Demo** tab: run prepared cases with the gate on or off; approve or reject held calls.
- **Live agent** tab: watch the worker agent handle a ticket, step by step, with each call checked by the gate. Needs an API key.
- **Results** tab: the latest benchmark results.

**Tests** (offline, no API key or cost):

```bash
python -m unittest discover tests
```

**Benchmark:**

```bash
python -m eval.run_benchmark --configs none keyword   # offline baselines, free
python -m eval.run_benchmark --allow-api --limit 3    # quick check of the Claude monitors
python -m eval.run_benchmark --allow-api              # full run (about 100 short calls), writes results/latest.md
```

**Command-line demo of one case:**

```bash
python demo.py --case II-01 --monitor agentgate
```

**Optional offline replay for presentations:** `python record_replay.py` saves real decisions for all 50 cases to `replay.json`, which the web demo can then play back without network access.

The model defaults to Claude Haiku 5.5 for speed. Set `AGENTGATE_MODEL` in `.env` to try another model. Calls made with the `--allow-api` flag, the live agent and live mode use your API credits; a full benchmark run costs a few cents with Haiku.

## Repository layout

```
agentgate/
  agent.py         worker agent: a Claude tool-use loop whose calls go through the gate
  monitors.py      monitors: no defense, keyword filter, Claude monitor (with or without policies)
  policy.py        policy store and retrieval
  proxy.py         the gate: runs a tool only after an allow or a human approval; audit log
  tools.py         mock tools (email, files, refunds, web, CRM); nothing real is contacted
benchmark/
  cases.json             50 cases: 30 attacks, 20 benign
  policies.json          10 company policies
  agent_scenarios.json   4 live-agent scenarios
  make_benchmark.py      regenerates cases.json and policies.json
  README.md              case format and scoring
eval/run_benchmark.py    runs every configuration and computes the metrics
results/                 the reported benchmark run
tests/                   offline unit tests
web/index.html           web demo page
server.py                web demo server (localhost only)
demo.py                  command-line demo of one case
record_replay.py         records decisions for offline replay
```

## Safety notes

- All tools are mocks, and every outside address uses the reserved `.example` domain. No real emails or payments are ever sent.
- The web server listens on 127.0.0.1 only.
- AgentGate reduces risk on our benchmark; it does not eliminate attacks. Adaptive attackers may bypass it.

## Limitations and next steps

- The benchmark is small (50 hand-written cases) and uses mock tools.
- The monitor is an LLM and can err in both directions (see BN-10 above).
- At about 1 second per decision, it is too slow for the 300 ms goal.
- Current Claude models often ignore injected instructions themselves, so the live agent rarely acts on a poisoned ticket; the benchmark's prepared calls stand in for a compromised or weaker agent.

Next: adaptive attacks written against the monitor, a larger benchmark, checking only high-risk tools to cut latency, and integration with agent frameworks and MCP servers.

## Team

| Member | Role |
|---|---|
| Vraj Soni | System lead: proxy, agents, policy store, documentation |
| Agatsya Patel | Evaluation lead: benchmark, harness, results, demo video |

## License

[MIT](LICENSE)
