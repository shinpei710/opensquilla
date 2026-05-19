"""Opt-in real-browser chat surface e2e without provider spend."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.webui_browser


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _npm() -> str:
    return "npm.cmd" if os.name == "nt" else "npm"


def _node() -> str:
    return "node.exe" if os.name == "nt" else "node"


def _install_playwright(work_dir: Path) -> None:
    result = subprocess.run(
        [_npm(), "--prefix", str(work_dir), "install", "playwright"],
        cwd=Path.cwd(),
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def _wait_for_health(port: int, server: subprocess.Popen[str]) -> None:
    url = f"http://127.0.0.1:{port}/health"
    deadline = time.monotonic() + 20.0
    last_error = ""
    while time.monotonic() < deadline:
        if server.poll() is not None:
            stdout = server.stdout.read() if server.stdout else ""
            stderr = server.stderr.read() if server.stderr else ""
            raise AssertionError(
                f"gateway exited early code={server.returncode}\nstdout={stdout}\nstderr={stderr}"
            )
        try:
            response = httpx.get(url, timeout=1.0)
            if response.status_code == 200 and response.json().get("ok") is True:
                return
        except Exception as exc:  # noqa: BLE001 - surfaced on timeout.
            last_error = str(exc)
        time.sleep(0.1)
    raise AssertionError(f"gateway did not become healthy: {last_error}")


def _stop_process(process: subprocess.Popen[str]) -> None:
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=8)


def test_per_turn_bubble_chip_differs_across_turns_in_real_browser(tmp_path: Path) -> None:
    """P4-AC6: per-turn .msg-meta__tokens chip reflects per-turn token counts.

    Two synthetic turns are injected via the RPC event bus (no LLM spend).
    Turn 1 uses input_tokens=11; turn 2 uses input_tokens=19.  The test
    asserts that:
    - chip[0].input != chip[1].input  (per-turn semantics, values differ)
    - chip[1].input >= chip[0].input  (monotonic across a session with growing
      context, satisfied trivially because 19 > 11)
    """
    if os.environ.get("OPENSQUILLA_WEBUI_BROWSER_CHAT_E2E") != "1":
        pytest.skip("set OPENSQUILLA_WEBUI_BROWSER_CHAT_E2E=1 to run chat browser e2e")

    port = _free_port()
    server_script = tmp_path / "webui_bubble_server.py"
    browser_script = tmp_path / "webui_bubble_browser.js"
    server_script.write_text(
        textwrap.dedent(
            f"""
            import uvicorn

            from opensquilla.gateway.app import create_gateway_app
            from opensquilla.gateway.config import AuthConfig, GatewayConfig

            config = GatewayConfig(
                host="127.0.0.1",
                port={port},
                auth=AuthConfig(mode="none"),
            )
            app = create_gateway_app(config)

            if __name__ == "__main__":
                uvicorn.run(app, host="127.0.0.1", port={port}, log_level="warning")
            """
        ),
        encoding="utf-8",
    )
    browser_script.write_text(
        textwrap.dedent(
            r"""
            const { chromium } = require("playwright");

            // Inject a synthetic streaming turn into the chat JS event bus.
            // text_delta fires through the named listener; done fires through the
            // wildcard (*) listener which is how chat.js processes terminal events.
            async function injectTurn(page, inputTokens, outputTokens) {
              await page.evaluate(
                ({ inputTokens, outputTokens }) => {
                  const rpc = App.getRpc();
                  const ls = rpc._listeners;

                  // 1. text_delta — named listener — creates the stream bubble
                  const deltaHandlers = ls.get("session.event.text_delta");
                  if (deltaHandlers) {
                    deltaHandlers.forEach(h => h({ text: "hi" }));
                  }

                  // 2. session.event.done — wildcard (*) listener — attaches
                  //    the .msg-meta__tokens chip to the finished bubble
                  const wildHandlers = ls.get("*");
                  if (wildHandlers) {
                    wildHandlers.forEach(h =>
                      h("session.event.done", {
                        input_tokens: inputTokens,
                        output_tokens: outputTokens,
                        text: "hi",
                      })
                    );
                  }
                },
                { inputTokens, outputTokens }
              );
            }

            (async () => {
              const browser = await chromium.launch({ headless: true });
              const page = await browser.newPage();
              const errors = [];
              page.on("pageerror", err => errors.push(String(err)));

              await page.goto(process.env.TARGET_URL, {
                waitUntil: "domcontentloaded",
                timeout: 30000,
              });
              await page.waitForSelector("#chat-textarea", { timeout: 15000 });

              // Wait for WebSocket RPC connection (chat.js needs it to register listeners)
              await page.waitForFunction(
                () =>
                  typeof App !== "undefined" &&
                  App.getRpc &&
                  App.getRpc()?.state === "connected",
                { timeout: 15000 }
              );

              // Turn 1: input=11, output=5
              await injectTurn(page, 11, 5);
              await page.waitForSelector(".msg-meta__tokens", { timeout: 5000 });

              // Turn 2: input=19, output=7
              await injectTurn(page, 19, 7);
              // Wait for the second chip to appear
              await page.waitForFunction(
                () => document.querySelectorAll(".msg-meta__tokens").length >= 2,
                { timeout: 5000 }
              );

              const chips = await page.evaluate(() =>
                Array.from(document.querySelectorAll(".msg-meta__tokens")).map(el => el.textContent)
              );

              // Parse "↑X ↓Y" into { input: X, output: Y }
              function parseChip(text) {
                const m = text.match(/↑(\d+(?:\.\d+)?[KMk]?)\s*↓(\d+(?:\.\d+)?[KMk]?)/);
                if (!m) return null;
                function tok(s) {
                  const n = parseFloat(s);
                  if (s.endsWith("K") || s.endsWith("k")) return Math.round(n * 1000);
                  if (s.endsWith("M")) return Math.round(n * 1000000);
                  return n;
                }
                return { input: tok(m[1]), output: tok(m[2]) };
              }

              const parsed = chips.slice(0, 2).map(parseChip);
              const result = {
                chipCount: chips.length,
                chip0: parsed[0],
                chip1: parsed[1],
                pageErrors: errors,
              };
              await browser.close();
              console.log(JSON.stringify(result));
            })().catch(err => {
              console.error(err && err.stack ? err.stack : String(err));
              process.exit(1);
            });
            """
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["OPENSQUILLA_STATE_DIR"] = str(tmp_path / "state")
    env["OPENSQUILLA_LOG_DIR"] = str(tmp_path / "logs")
    server = subprocess.Popen(
        [sys.executable, str(server_script)],
        cwd=Path.cwd(),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        _wait_for_health(port, server)
        _install_playwright(tmp_path)
        result = subprocess.run(
            [_node(), str(browser_script)],
            cwd=tmp_path,
            env=dict(env, TARGET_URL=f"http://127.0.0.1:{port}/control/chat"),
            check=False,
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert result.returncode == 0, result.stderr or result.stdout
        payload = json.loads(result.stdout.strip().splitlines()[-1])
    finally:
        _stop_process(server)

    assert payload["pageErrors"] == [], payload["pageErrors"]
    assert payload["chipCount"] >= 2, f"expected >=2 chips, got {payload['chipCount']}"
    chip0 = payload["chip0"]
    chip1 = payload["chip1"]
    assert chip0 is not None, "chip0 did not parse"
    assert chip1 is not None, "chip1 did not parse"
    # Per-turn semantics: each bubble shows the tokens for that turn, not the
    # session accumulator, so consecutive turns with different token counts must
    # produce different chip values.
    assert chip0["input"] != chip1["input"], (
        f"chip input_tokens should differ between turns: chip0={chip0}, chip1={chip1}"
    )
    # Monotonic: second turn's per-turn input >= first turn's (19 > 11 by construction)
    assert chip1["input"] >= chip0["input"], (
        f"chip1.input ({chip1['input']}) should be >= chip0.input ({chip0['input']})"
    )


def test_chat_view_loads_and_reaches_gateway_http_status_in_real_browser(tmp_path: Path) -> None:
    if os.environ.get("OPENSQUILLA_WEBUI_BROWSER_CHAT_E2E") != "1":
        pytest.skip("set OPENSQUILLA_WEBUI_BROWSER_CHAT_E2E=1 to run chat browser e2e")

    port = _free_port()
    server_script = tmp_path / "webui_chat_server.py"
    browser_script = tmp_path / "webui_chat_browser.js"
    server_script.write_text(
        textwrap.dedent(
            f"""
            import uvicorn

            from opensquilla.gateway.app import create_gateway_app
            from opensquilla.gateway.config import AuthConfig, GatewayConfig

            config = GatewayConfig(
                host="127.0.0.1",
                port={port},
                auth=AuthConfig(mode="none"),
            )
            app = create_gateway_app(config)

            if __name__ == "__main__":
                uvicorn.run(app, host="127.0.0.1", port={port}, log_level="warning")
            """
        ),
        encoding="utf-8",
    )
    browser_script.write_text(
        textwrap.dedent(
            """
            const { chromium } = require("playwright");

            (async () => {
              const browser = await chromium.launch({ headless: true });
              const page = await browser.newPage();
              const errors = [];
              page.on("pageerror", err => errors.push(String(err)));
              const response = await page.goto(process.env.TARGET_URL, {
                waitUntil: "domcontentloaded",
                timeout: 30000,
              });
              await page.waitForSelector("#chat-textarea", { timeout: 15000 });
              const status = await page.evaluate(async () => {
                const res = await fetch("/api/system/status");
                return await res.json();
              });
              const bodyText = await page.locator("body").innerText();
              const result = {
                statusCode: response ? response.status() : 0,
                title: await page.title(),
                textareaCount: await page.locator("#chat-textarea").count(),
                sendButtonCount: await page.locator("#chat-btn-send").count(),
                activeChatNav: await page.locator('.nav-item.is-active[data-path="/chat"]').count(),
                gatewayStatus: status.status,
                authMode: status.auth_mode,
                hasRemovedToolName:
                  bodyText.includes("generate_image") ||
                  bodyText.includes("spawn_subagent") ||
                  bodyText.includes("send_message"),
                pageErrors: errors,
              };
              await browser.close();
              console.log(JSON.stringify(result));
            })().catch(err => {
              console.error(err && err.stack ? err.stack : String(err));
              process.exit(1);
            });
            """
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["OPENSQUILLA_STATE_DIR"] = str(tmp_path / "state")
    env["OPENSQUILLA_LOG_DIR"] = str(tmp_path / "logs")
    server = subprocess.Popen(
        [sys.executable, str(server_script)],
        cwd=Path.cwd(),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        _wait_for_health(port, server)
        _install_playwright(tmp_path)
        result = subprocess.run(
            [_node(), str(browser_script)],
            cwd=tmp_path,
            env=dict(env, TARGET_URL=f"http://127.0.0.1:{port}/control/chat"),
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr or result.stdout
        payload = json.loads(result.stdout.strip().splitlines()[-1])
    finally:
        _stop_process(server)

    assert payload == {
        "statusCode": 200,
        "title": "OpenSquilla Control",
        "textareaCount": 1,
        "sendButtonCount": 1,
        "activeChatNav": 1,
        "gatewayStatus": "running",
        "authMode": "none",
        "hasRemovedToolName": False,
        "pageErrors": [],
    }


def test_chat_compaction_events_render_recoverable_toasts_in_real_browser(
    tmp_path: Path,
) -> None:
    if os.environ.get("OPENSQUILLA_WEBUI_BROWSER_CHAT_E2E") != "1":
        pytest.skip("set OPENSQUILLA_WEBUI_BROWSER_CHAT_E2E=1 to run chat browser e2e")

    port = _free_port()
    server_script = tmp_path / "webui_chat_compaction_server.py"
    browser_script = tmp_path / "webui_chat_compaction_browser.js"
    server_script.write_text(
        textwrap.dedent(
            f"""
            import uvicorn

            from opensquilla.gateway.app import create_gateway_app
            from opensquilla.gateway.config import AuthConfig, GatewayConfig

            config = GatewayConfig(
                host="127.0.0.1",
                port={port},
                auth=AuthConfig(mode="none"),
            )
            app = create_gateway_app(config)

            if __name__ == "__main__":
                uvicorn.run(app, host="127.0.0.1", port={port}, log_level="warning")
            """
        ),
        encoding="utf-8",
    )
    browser_script.write_text(
        textwrap.dedent(
            r"""
            const { chromium } = require("playwright");

            async function emitCompaction(page, payload, meta = {}) {
              await page.evaluate(
                ({ payload, meta }) => {
                  const rpc = App.getRpc();
                  const handlers = rpc._listeners.get("session.event.compaction");
                  if (handlers) {
                    handlers.forEach(h => h(payload, meta));
                  }
                },
                { payload, meta }
              );
            }

            (async () => {
              const browser = await chromium.launch({ headless: true });
              const page = await browser.newPage();
              const errors = [];
              page.on("pageerror", err => errors.push(String(err)));

              await page.goto(process.env.TARGET_URL, {
                waitUntil: "domcontentloaded",
                timeout: 30000,
              });
              await page.waitForSelector("#chat-textarea", { timeout: 15000 });
              await page.waitForFunction(
                () =>
                  typeof App !== "undefined" &&
                  App.getRpc &&
                  App.getRpc()?.state === "connected",
                { timeout: 15000 }
              );

              await emitCompaction(page, { status: "started", source: "manual" });
              await emitCompaction(page, { status: "skipped", source: "manual" });
              await page.waitForTimeout(250);

              await emitCompaction(page, {
                status: "completed",
                source: "manual",
                tokens_before: 3500,
                tokens_after: 1300,
              });
              await page.waitForFunction(
                () => document.body.innerText.includes("Context compacted"),
                { timeout: 5000 }
              );

              await emitCompaction(
                page,
                { status: "failed", source: "manual", message: "old replay" },
                { replayed: true }
              );
              await page.waitForTimeout(250);

              const bodyText = await page.locator("body").innerText();
              const result = {
                hasStartedToast: bodyText.includes("Checking whether compaction is needed..."),
                hasSkippedToast: bodyText.includes("No compaction needed"),
                hasCompletedToast: bodyText.includes("Context compacted"),
                hasReplayedFailureToast: bodyText.includes("old replay"),
                pageErrors: errors,
              };
              await browser.close();
              console.log(JSON.stringify(result));
            })().catch(err => {
              console.error(err && err.stack ? err.stack : String(err));
              process.exit(1);
            });
            """
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["OPENSQUILLA_STATE_DIR"] = str(tmp_path / "state")
    env["OPENSQUILLA_LOG_DIR"] = str(tmp_path / "logs")
    server = subprocess.Popen(
        [sys.executable, str(server_script)],
        cwd=Path.cwd(),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        _wait_for_health(port, server)
        _install_playwright(tmp_path)
        result = subprocess.run(
            [_node(), str(browser_script)],
            cwd=tmp_path,
            env=dict(env, TARGET_URL=f"http://127.0.0.1:{port}/control/chat"),
            check=False,
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert result.returncode == 0, result.stderr or result.stdout
        payload = json.loads(result.stdout.strip().splitlines()[-1])
    finally:
        _stop_process(server)

    assert payload == {
        "hasStartedToast": False,
        "hasSkippedToast": False,
        "hasCompletedToast": True,
        "hasReplayedFailureToast": False,
        "pageErrors": [],
    }
