from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from opensquilla.memory.dream import Dream


def _dream(workspace, *, provider=object()):
    return Dream(
        workspace=workspace,
        provider=provider,
        model="test",
        tool_registry=None,
        session_lock=None,
        config=SimpleNamespace(
            max_batch_size=10,
            min_batch_size=1,
            input_slimming="off",
            preview_mode=False,
            dry_run=False,
        ),
    )


def test_dream_uses_workspace_root_memory_md_for_curated_memory(tmp_path):
    root_memory = tmp_path / "MEMORY.md"
    nested_memory_dir = tmp_path / "memory"
    nested_memory = nested_memory_dir / "MEMORY.md"
    candidate = nested_memory_dir / "candidate.md"
    root_memory.write_text("root curated marker", encoding="utf-8")
    nested_memory_dir.mkdir()
    nested_memory.write_text("nested stale marker", encoding="utf-8")
    candidate.write_text("candidate note", encoding="utf-8")

    dream = _dream(tmp_path)
    prompt, _chars, _phase = dream._phase1_prompt([candidate])

    assert dream.memory_md == root_memory
    assert "root curated marker" in prompt
    assert "nested stale marker" not in prompt


class _Response:
    def __init__(self, content: str) -> None:
        self.content = content


class _MutatingPhase2Provider:
    def __init__(self, candidate) -> None:
        self.calls = 0
        self.candidate = candidate

    async def complete(self, *, messages, max_tokens):
        self.calls += 1
        if self.calls == 1:
            return _Response("consolidate candidate")
        self.candidate.write_text("changed by another dream run\n", encoding="utf-8")
        return _Response(json.dumps({"edits": [{"op": "append", "text": "\nstale fact"}]}))


class _AppendingPhase2Provider:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, *, messages, max_tokens):
        self.calls += 1
        if self.calls == 1:
            return _Response("consolidate candidate")
        return _Response(json.dumps({"edits": [{"op": "append", "text": "\nfresh fact"}]}))


@pytest.mark.asyncio
async def test_dream_rejects_stale_phase2_plan_when_candidate_changes(tmp_path):
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    memory_md = tmp_path / "MEMORY.md"
    memory_md.write_text("baseline\n", encoding="utf-8")
    candidate = memory_dir / "candidate.md"
    candidate.write_text("original candidate\n", encoding="utf-8")

    provider = _MutatingPhase2Provider(candidate)
    dream = _dream(tmp_path, provider=provider)

    result = await dream.run()

    assert result.phase1_status == "ok"
    assert result.phase2_status == "conflict"
    assert "stale" in (result.error or "")
    assert memory_md.read_text(encoding="utf-8") == "baseline\n"
    assert candidate.exists()
    assert candidate.read_text(encoding="utf-8") == "changed by another dream run\n"
    assert dream.cursor.load() == 0.0
    assert result.files_deleted == 0
    assert result.files_processed == 0
    assert result.edit_receipt_path is not None


@pytest.mark.asyncio
async def test_dream_keeps_phase2_error_when_cursor_cleanup_fails(tmp_path, monkeypatch):
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    memory_md = tmp_path / "MEMORY.md"
    memory_md.write_text("baseline\n", encoding="utf-8")
    candidate = memory_dir / "candidate.md"
    candidate.write_text("candidate\n", encoding="utf-8")
    dream = _dream(tmp_path, provider=_AppendingPhase2Provider())

    def fail_save(_ts: float) -> None:
        raise OSError("cursor denied")

    monkeypatch.setattr(dream.cursor, "save", fail_save)

    result = await dream.run()

    assert result.phase2_status == "error"
    assert "phase2_cleanup" in (result.error or "")
