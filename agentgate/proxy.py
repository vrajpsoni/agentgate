"""The proxy: nothing reaches a tool without a decision."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .monitors import Decision, ToolRequest
from .tools import MockTools


@dataclass
class ProxyResult:
    decision: Decision
    executed: bool
    tool_result: dict | None = None
    approved_by_human: bool | None = None


class AgentGate:
    def __init__(self, monitor, tools: MockTools, audit_path: str | Path | None = None,
                 approver: Callable[[ToolRequest, Decision], bool] | None = None) -> None:
        """`approver` is called for ask_human decisions and returns True to approve.
        With no approver, ask_human is treated as not approved (fail closed)."""
        self.monitor = monitor
        self.tools = tools
        self.audit_path = Path(audit_path) if audit_path else None
        self.approver = approver

    def handle(self, req: ToolRequest) -> ProxyResult:
        d = self.monitor.check(req)
        executed, result, approved = False, None, None
        if d.decision == "allow":
            result, executed = self.tools.execute(req.tool, req.args), True
        elif d.decision == "ask_human":
            approved = bool(self.approver(req, d)) if self.approver else False
            if approved:
                result, executed = self.tools.execute(req.tool, req.args), True
        self._audit(req, d, executed, approved)
        return ProxyResult(d, executed, result, approved)

    def _audit(self, req, d, executed, approved) -> None:
        if not self.audit_path:
            return
        record = {
            "ts": time.time(), "tool": req.tool, "args": req.args, "user_task": req.user_task,
            "decision": d.to_dict(), "executed": executed, "approved_by_human": approved,
        }
        with self.audit_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
