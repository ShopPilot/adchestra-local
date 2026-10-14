"""Mutation audit logging — every write operation is logged.

The default sink appends JSON lines to a local file (the OSS behavior).
The hosted server swaps in a database-backed sink via
:func:`set_audit_sink`; the ``log_mutation`` call sites in the write
path stay unchanged either way.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol


class AuditSink(Protocol):
    """Destination for audit records.

    ``log_file`` is the per-tenant path from ``config.safety.log_file``;
    file-based sinks use it, database sinks may ignore it.
    """

    def record(self, entry: dict[str, Any], *, log_file: str) -> None: ...


class FileAuditSink:
    """Default sink: append JSON lines to a local audit log file."""

    def record(self, entry: dict[str, Any], *, log_file: str) -> None:
        path = Path(log_file).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(entry) + "\n")


_active_sink: AuditSink = FileAuditSink()


def set_audit_sink(sink: AuditSink) -> None:
    """Swap the audit sink (the hosted server plugs in a persistent one)."""
    global _active_sink
    _active_sink = sink


def get_audit_sink() -> AuditSink:
    return _active_sink


def log_mutation(
    log_file: str,
    *,
    operation: str,
    customer_id: str = "",
    entity_type: str = "",
    entity_id: str = "",
    changes: dict[str, Any] | None = None,
    dry_run: bool = True,
    result: str = "success",
    error: str = "",
) -> None:
    """Record a mutation via the active audit sink."""
    from adchestra_local.runtime import current_tenant

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tenant": current_tenant(),
        "operation": operation,
        "customer_id": customer_id,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "changes": changes or {},
        "dry_run": dry_run,
        "result": result,
        "error": error,
    }

    _active_sink.record(record, log_file=log_file)
