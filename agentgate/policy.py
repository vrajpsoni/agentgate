"""Policy store with hybrid retrieval: tool metadata + TF-IDF over policy text.

No external dependencies. Swap `retrieve` for an embedding store later if you want
semantic search; the monitor only needs a list of policies back.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


def _tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    # crude stemming so "refunds" matches "refund"
    return [w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words]


@dataclass
class Policy:
    id: str
    name: str
    text: str
    governs: list[str]


class PolicyStore:
    def __init__(self, policies: list[Policy]) -> None:
        self.policies = policies
        self._docs = [Counter(_tokens(p.name + " " + p.text)) for p in policies]
        n = len(policies)
        df: Counter = Counter()
        for doc in self._docs:
            df.update(doc.keys())
        self._idf = {t: math.log((1 + n) / (1 + c)) + 1 for t, c in df.items()}

    @classmethod
    def from_json(cls, path: str | Path) -> "PolicyStore":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls([Policy(d["id"], d["name"], d["text"], d.get("governs", [])) for d in data])

    def _score(self, query: Counter, doc: Counter) -> float:
        dot = sum(query[t] * self._idf.get(t, 0) * doc[t] * self._idf.get(t, 0) for t in query if t in doc)
        qn = math.sqrt(sum((c * self._idf.get(t, 0)) ** 2 for t, c in query.items())) or 1.0
        dn = math.sqrt(sum((c * self._idf.get(t, 0)) ** 2 for t, c in doc.items())) or 1.0
        return dot / (qn * dn)

    def retrieve(self, query: str, tool: str, k: int = 3) -> list[Policy]:
        """Policies that govern `tool`, plus the top-k lexical matches for `query`.
        Policies governing "*" (e.g. untrusted-content) are always included."""
        q = Counter(_tokens(query))
        chosen: dict[str, Policy] = {}
        for p in self.policies:
            if "*" in p.governs or tool in p.governs:
                chosen[p.id] = p
        ranked = sorted(zip(self.policies, self._docs), key=lambda pd: self._score(q, pd[1]), reverse=True)
        for p, _ in ranked[:k]:
            chosen.setdefault(p.id, p)
        return sorted(chosen.values(), key=lambda p: p.id)

    @staticmethod
    def render(policies: list[Policy], context: dict) -> str:
        class _Defaults(dict):
            def __missing__(self, key):
                return "{" + key + "}"

        fill = _Defaults(context)
        return "\n".join(f"{p.id} ({p.name}): {p.text.format_map(fill)}" for p in policies)
