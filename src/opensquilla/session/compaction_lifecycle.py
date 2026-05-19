"""Shared compaction lifecycle helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

SAFE_FLUSH_OUTPUT_COVERAGE_STATUSES: Final[frozenset[str]] = frozenset(
    {"ok", "unverifiable"}
)
SAFE_FLUSH_OBLIGATION_STATUSES: Final[frozenset[str]] = frozenset(
    {"ok", "backfilled", "unverifiable"}
)


@dataclass(frozen=True)
class CompactionLifecycleResult:
    compacted: bool
    refused: bool
    reason: str | None = None
    tokens_before: int | None = None
    tokens_after: int | None = None
    remaining_budget_tokens: int | None = None
    removed_count: int = 0
    kept_count: int = 0
    summary_len: int = 0
    summary_source: str = "unknown"
    flush_receipt: Any = None


def _receipt_value(receipt: Any, name: str, default: Any) -> Any:
    if isinstance(receipt, Mapping):
        return receipt.get(name, default)
    return getattr(receipt, name, default)


def _receipt_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def flush_receipt_allows_destructive_compaction(receipt: Any) -> bool:
    if _receipt_value(receipt, "mode", None) != "llm":
        return False
    if _receipt_int(_receipt_value(receipt, "indexed_chunk_count", 0)) <= 0:
        return False
    integrity_status = str(
        _receipt_value(receipt, "integrity_status", "unverified") or "unverified"
    )
    if integrity_status != "ok":
        return False
    output_coverage_status = str(
        _receipt_value(receipt, "output_coverage_status", "unverified")
        or "unverified"
    )
    if output_coverage_status not in SAFE_FLUSH_OUTPUT_COVERAGE_STATUSES:
        return False
    if _receipt_int(_receipt_value(receipt, "invalid_candidate_count", 0)) > 0:
        return False
    if _receipt_value(receipt, "candidate_missing_ids", []):
        return False
    obligation_status = str(
        _receipt_value(receipt, "obligation_status", "unverified") or "unverified"
    )
    if obligation_status not in SAFE_FLUSH_OBLIGATION_STATUSES:
        return False
    return not _receipt_value(receipt, "obligation_missing_ids", [])


def pre_compaction_flush_enabled(config: Any) -> bool:
    from opensquilla.memory.flush_config import is_session_flush_enabled

    if not is_session_flush_enabled():
        return False
    memory_cfg = getattr(config, "memory", None)
    return bool(getattr(memory_cfg, "flush_enabled", True))


def pre_compaction_flush_requires_safe_receipt(config: Any) -> bool:
    memory_cfg = getattr(config, "memory", None)
    if memory_cfg is None:
        return False
    return bool(getattr(memory_cfg, "flush_compaction_requires_safe_receipt", False))


def flush_receipt_to_dict(receipt: Any) -> dict[str, Any]:
    if receipt is None:
        return {}
    to_dict = getattr(receipt, "to_dict", None)
    if callable(to_dict):
        return dict(to_dict())
    if isinstance(receipt, Mapping):
        return dict(receipt)
    return dict(vars(receipt))
