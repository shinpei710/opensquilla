"""ChannelManager lifecycle diagnostics."""

from __future__ import annotations

import pytest

from opensquilla.channels.manager import ChannelManager


class _FailingChannel:
    async def start(self) -> None:
        raise RuntimeError("Install Feishu support with `opensquilla[feishu]`")


@pytest.mark.asyncio
async def test_start_all_retains_start_exception_details():
    manager = ChannelManager({"feishu": _FailingChannel()}, None, None)

    results = await manager.start_all()

    assert results == {"feishu": False}
    assert manager.start_errors()["feishu"] == {
        "error_type": "RuntimeError",
        "error": "Install Feishu support with `opensquilla[feishu]`",
        "exception": "RuntimeError('Install Feishu support with `opensquilla[feishu]`')",
    }
