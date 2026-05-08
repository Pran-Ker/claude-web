"""Structured errors. Each carries a `kind`, a human message, and a `hint`
that tells the agent what to do next. The CLI serializes these to JSON;
library callers can catch them by type."""

from __future__ import annotations


class WebAgentError(Exception):
    kind: str = "error"

    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.message = message
        self.hint = hint

    def to_dict(self) -> dict:
        return {
            "ok": False,
            "kind": self.kind,
            "error": self.message,
            "hint": self.hint,
        }


class TransportError(WebAgentError):
    kind = "transport_error"


class JSExecutionError(WebAgentError):
    kind = "js_exception"


class SnapshotNotFound(WebAgentError):
    kind = "snapshot_not_found"


class StaleHandle(WebAgentError):
    kind = "stale_handle"


class ElementNotActionable(WebAgentError):
    kind = "element_not_actionable"


class InvalidArguments(WebAgentError):
    kind = "invalid_arguments"
