from __future__ import annotations

from opensquilla.channels.command_registry import DEFAULT_COMMAND_REGISTRY
from opensquilla.engine.commands import DEFAULT_REGISTRY, Surface


def test_channel_command_names_include_usage_and_registry_words() -> None:
    expected = {
        word.lstrip("/").lower()
        for cmd in DEFAULT_REGISTRY.for_surface(Surface.CHANNEL)
        for word in cmd.words()
    }

    assert "usage" in DEFAULT_COMMAND_REGISTRY.command_names
    assert expected <= DEFAULT_COMMAND_REGISTRY.command_names
