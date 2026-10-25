"""Mock tools for the customer-support agent. Nothing here touches the real world."""
from __future__ import annotations


class MockTools:
    """Records every call it executes so tests can assert that blocked calls never ran."""

    def __init__(self, files: dict[str, str] | None = None) -> None:
        """`files` maps paths to mock contents, so an agent scenario can plant a ticket to read."""
        self.files = files or {}
        self.executed: list[dict] = []
        self._handlers = {
            "send_email": self._send_email,
            "read_file": self._read_file,
            "write_file": self._write_file,
            "issue_refund": self._issue_refund,
            "fetch_url": self._fetch_url,
            "lookup_customer": self._lookup_customer,
            "lookup_order": self._lookup_order,
        }

    @property
    def names(self) -> list[str]:
        return sorted(self._handlers)

    def execute(self, tool: str, args: dict) -> dict:
        handler = self._handlers.get(tool)
        if handler is None:
            return {"ok": False, "error": f"unknown tool: {tool}"}
        self.executed.append({"tool": tool, "args": args})
        return handler(args)

    # Each handler pretends to succeed.
    def _send_email(self, a):
        return {"ok": True, "mock": True, "sent_to": a.get("to")}

    def _read_file(self, a):
        return {"ok": True, "mock": True, "path": a.get("path"),
                "content": self.files.get(a.get("path"), "[mock file content]")}

    def _write_file(self, a):
        return {"ok": True, "mock": True, "path": a.get("path")}

    def _issue_refund(self, a):
        return {"ok": True, "mock": True, "refunded": a.get("amount")}

    def _fetch_url(self, a):
        return {"ok": True, "mock": True, "url": a.get("url"), "body": "[mock page]"}

    def _lookup_customer(self, a):
        return {"ok": True, "mock": True, "customer": a.get("customer_id") or a.get("query")}

    def _lookup_order(self, a):
        return {"ok": True, "mock": True, "order": a.get("order_id")}
