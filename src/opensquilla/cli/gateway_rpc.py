"""Shared gateway RPC helpers for CLI commands."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from typing import Any

import typer

from opensquilla.cli.output import emit_error
from opensquilla.cli.url_utils import normalize_gateway_url


def default_gateway_url() -> str:
    """Return the configured gateway WebSocket URL."""

    return normalize_gateway_url(
        os.environ.get("OPENSQUILLA_GATEWAY_URL", "ws://localhost:18791/ws")
    )


def rpc_error_exit_code(code: str | None) -> int:
    """Map gateway error codes to the CLI exit-code convention."""

    normalized = (code or "").upper()
    if normalized in {"INVALID_REQUEST", "NOT_FOUND", "METHOD_NOT_FOUND"}:
        return 2
    if normalized in {"CONFLICT", "STATE_CONFLICT", "LIFECYCLE_CONFLICT"}:
        return 3
    return 1


async def run_gateway_call(
    action: Callable[[Any], Awaitable[Any]],
    *,
    gateway_url: str | None = None,
    json_output: bool = False,
) -> Any:
    """Connect to the gateway, run ``action(client)``, and close cleanly."""

    from opensquilla.cli import gateway_client as gateway_client_module

    client = gateway_client_module.GatewayClient()
    try:
        target_url = (
            default_gateway_url()
            if gateway_url is None
            else normalize_gateway_url(gateway_url)
        )
        await client.connect(target_url)
        return await action(client)
    except SystemExit as exc:
        message = str(exc)
        emit_error(message, json_output=json_output, code="GATEWAY_UNAVAILABLE")
        raise typer.Exit(1) from exc
    except gateway_client_module.GatewayRPCError as exc:
        emit_error(
            exc.message,
            json_output=json_output,
            code=exc.code,
            details=exc.data,
        )
        raise typer.Exit(rpc_error_exit_code(exc.code)) from exc
    except (ConnectionError, OSError) as exc:
        emit_error(str(exc), json_output=json_output, code="GATEWAY_UNAVAILABLE")
        raise typer.Exit(1) from exc
    finally:
        await client.close()


def run_gateway_sync(
    action: Callable[[Any], Awaitable[Any]],
    *,
    gateway_url: str | None = None,
    json_output: bool = False,
) -> Any:
    """Synchronous Typer-friendly wrapper around :func:`run_gateway_call`."""

    return asyncio.run(
        run_gateway_call(action, gateway_url=gateway_url, json_output=json_output)
    )


def confirm_or_exit(prompt: str, *, yes: bool, json_output: bool = False) -> None:
    """Require confirmation unless ``--yes`` was passed."""

    if yes:
        return
    if json_output:
        emit_error(
            "confirmation required; rerun with --yes to execute",
            json_output=True,
            code="CONFIRMATION_REQUIRED",
        )
        raise typer.Exit(2)
    typer.confirm(prompt, abort=True)
