"""turn_finalizer renders paused MetaResult without invoking failure path."""

from __future__ import annotations

from opensquilla.engine.turn_runner.turn_finalizer_stage import (
    render_paused_outcome,
)
from opensquilla.skills.meta.types import (
    ClarifyField,
    ClarifyStepConfig,
    MetaPaused,
    MetaResult,
)


def test_render_paused_outcome_includes_intro_and_fields():
    cfg = ClarifyStepConfig(
        mode="form",
        fields=(
            ClarifyField(name="destination", type="string", required=True,
                         prompt="目的地"),
            ClarifyField(name="days", type="int", required=True, min=1, max=14,
                         prompt="天数"),
        ),
        intro="需要确认几件事。",
    )
    paused = MetaPaused(run_id="r1", step_id="collect", schema=cfg)
    result = MetaResult(ok=False, paused=True, paused_payload=paused)

    text = render_paused_outcome(result)
    assert "需要确认几件事。" in text
    assert "destination" in text
    assert "目的地" in text
    assert "days" in text
    assert "天数" in text


def test_render_paused_outcome_returns_final_text_when_not_paused():
    """Non-paused result with final_text should return that text verbatim."""
    result = MetaResult(ok=True, final_text="done", paused=False)
    assert render_paused_outcome(result) == "done"
