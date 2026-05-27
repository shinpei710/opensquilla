from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

CheckpointRole = Literal[
    "user",
    "assistant",
    "tool_call",
    "tool_result",
    "system_notice",
    "error",
]
CheckpointContentType = Literal["text", "json", "binary_ref", "redacted"]
CheckpointStatus = Literal["ok", "error", "truncated", "redacted"]


@dataclass(frozen=True)
class CheckpointEvent:
    schema_version: int
    event_id: str
    session_key: str
    session_id: str
    turn_id: str
    sequence: int
    timestamp_ms: int
    role: CheckpointRole
    content_type: CheckpointContentType
    content: str
    summary: str | None
    tool_name: str | None
    tool_call_id: str | None
    status: CheckpointStatus
    token_estimate: int
    source: str
    attachments: list[dict]
    content_hash: str

    def to_json_dict(self) -> dict:
        payload = asdict(self)
        if not payload["content_hash"]:
            payload["content_hash"] = checkpoint_event_hash(self.content)
        return payload


@dataclass(frozen=True)
class CheckpointWriteResult:
    relative_path: str
    event_count: int
    content_hash: str


def checkpoint_event_hash(content: str) -> str:
    normalized = str(content or "").strip().encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


def _safe_path_component(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-")
    if safe in {"", ".", ".."}:
        return "unknown"
    return safe


def checkpoint_relative_path(*, session_key: str, turn_id: str) -> Path:
    return (
        Path("memory")
        / ".checkpoints"
        / _safe_path_component(session_key)
        / f"{_safe_path_component(turn_id)}.jsonl"
    )


def serialize_checkpoint_event(event: CheckpointEvent) -> str:
    return json.dumps(event.to_json_dict(), ensure_ascii=False, sort_keys=True)


def append_checkpoint_events(
    workspace: Path,
    events: list[CheckpointEvent],
) -> CheckpointWriteResult:
    """Write one complete turn checkpoint JSONL snapshot.

    Rewriting the same serialized body is idempotent: if the target already has
    the same body hash, this returns the existing result without duplicating
    lines.
    """
    if not events:
        raise ValueError("checkpoint events cannot be empty")

    first_event = events[0]
    if any(
        event.session_key != first_event.session_key
        or event.turn_id != first_event.turn_id
        for event in events
    ):
        raise ValueError("checkpoint events must share session_key and turn_id")

    relative_path = checkpoint_relative_path(
        session_key=first_event.session_key,
        turn_id=first_event.turn_id,
    )
    body = "".join(f"{serialize_checkpoint_event(event)}\n" for event in events)
    body_bytes = body.encode("utf-8")
    content_hash = hashlib.sha256(body_bytes).hexdigest()
    result = CheckpointWriteResult(
        relative_path=relative_path.as_posix(),
        event_count=len(events),
        content_hash=content_hash,
    )

    target_path = workspace / relative_path
    if (
        target_path.exists()
        and hashlib.sha256(target_path.read_bytes()).hexdigest() == content_hash
    ):
        return result

    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            delete=False,
            dir=target_path.parent,
            prefix=f".{target_path.name}.",
            suffix=".tmp",
            mode="w",
            encoding="utf-8",
        ) as temp_file:
            temp_file.write(body)
            temp_path = Path(temp_file.name)
        os.replace(temp_path, target_path)
    except Exception:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise
    return result
