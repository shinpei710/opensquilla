"""Shared tool-result budget helpers.

The budget applies at tool boundaries, not at skill boundaries, so installed
skills cannot bypass it by asking for more fetches or larger outputs.

Lives at the top level (rather than inside ``opensquilla.tools``) so that the
engine layer can import these helpers without triggering the tool-registry
side effect in ``opensquilla.tools.__init__``. See
``tests/test_public_tool_surface.py::test_engine_types_import_does_not_register_builtin_tools``.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

WEB_FETCH_MIN_MAX_CHARS = 100


class ToolResultBudgetClass(StrEnum):
    EXTERNAL = "external"
    LOCAL = "local"
    ARTIFACT = "artifact"
    ERROR = "error"
    CONTROL = "control"
    UNKNOWN = "unknown"


EXTERNAL_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "http_request",
        "web_fetch",
        "web_search",
    }
)

CONTROL_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "sessions_yield",
    }
)


@dataclass(frozen=True)
class ToolResultBudgetPolicy:
    max_single_tool_result_chars: int = 16_000
    max_single_external_result_chars: int = 12_000
    max_tool_result_chars_per_turn: int = 96_000
    max_external_tool_result_chars_per_turn: int = 48_000
    min_error_result_chars: int = 512
    min_control_result_chars: int = 512
    max_web_fetch_chars: int = 12_000
    max_web_search_results: int = 10


DEFAULT_TOOL_RESULT_BUDGET_POLICY = ToolResultBudgetPolicy()


@dataclass(frozen=True)
class ToolRunBudgetPolicy:
    max_web_search_calls_per_turn: int | None = 8
    max_web_fetch_calls_per_turn: int | None = 20
    max_external_text_chars_per_turn: int | None = 300_000
    max_single_fetch_chars: int | None = 20_000


DEFAULT_TOOL_RUN_BUDGET_POLICY = ToolRunBudgetPolicy()


class ToolRunBudgetExceededError(RuntimeError):
    """Raised when a tool call would exceed the per-turn run budget."""

    def __init__(self, tool_name: str, message: str) -> None:
        super().__init__(message)
        self.tool_name = tool_name


@dataclass(frozen=True)
class ToolRunBudgetReservation:
    tool_name: str
    arguments: dict[str, Any]
    reserved_external_text_chars: int = 0
    counted_as_fetch: bool = False
    counted_as_search: bool = False
    counted_as_external_text: bool = False


class ToolRunBudgetTracker:
    """Concurrency-safe per-turn accounting for tool calls and raw text."""

    def __init__(self, policy: ToolRunBudgetPolicy | None = None) -> None:
        self.policy = policy or DEFAULT_TOOL_RUN_BUDGET_POLICY
        self._lock = asyncio.Lock()
        self._web_search_calls_used = 0
        self._web_fetch_calls_used = 0
        self._external_text_chars_used = 0
        self._external_text_chars_reserved = 0

    async def reserve_tool_call(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ToolRunBudgetReservation:
        args = dict(arguments)
        if tool_name == "web_search":
            async with self._lock:
                self._check_call_budget(
                    tool_name=tool_name,
                    used=self._web_search_calls_used,
                    limit=self.policy.max_web_search_calls_per_turn,
                )
                self._web_search_calls_used += 1
            return ToolRunBudgetReservation(
                tool_name=tool_name,
                arguments=args,
                counted_as_search=True,
                counted_as_external_text=True,
            )

        if tool_name not in EXTERNAL_TOOL_NAMES:
            return ToolRunBudgetReservation(tool_name=tool_name, arguments=args)

        async with self._lock:
            self._check_call_budget(
                tool_name=tool_name,
                used=self._web_fetch_calls_used,
                limit=self.policy.max_web_fetch_calls_per_turn,
            )
            reserved = self._reserve_external_text_budget(tool_name, args)
            self._web_fetch_calls_used += 1
            return ToolRunBudgetReservation(
                tool_name=tool_name,
                arguments=args,
                reserved_external_text_chars=reserved,
                counted_as_fetch=True,
                counted_as_external_text=True,
            )

    async def commit_tool_result(
        self,
        reservation: ToolRunBudgetReservation,
        content: Any,
    ) -> None:
        if not reservation.counted_as_external_text:
            return
        text = content if isinstance(content, str) else str(content)
        async with self._lock:
            self._release_external_reservation(reservation)
            self._external_text_chars_used += len(text)
            limit = self.policy.max_external_text_chars_per_turn
            if limit is not None and self._external_text_chars_used > limit:
                raise ToolRunBudgetExceededError(
                    reservation.tool_name,
                    (
                        "External tool text returned by this turn exceeded the "
                        f"run budget ({self._external_text_chars_used}>{limit})."
                    ),
                )

    async def abort_tool_result(self, reservation: ToolRunBudgetReservation) -> None:
        if (
            not reservation.counted_as_fetch
            and not reservation.counted_as_search
            and not reservation.counted_as_external_text
        ):
            return
        async with self._lock:
            self._release_external_reservation(reservation)
            if reservation.counted_as_fetch:
                self._web_fetch_calls_used = max(0, self._web_fetch_calls_used - 1)
            if reservation.counted_as_search:
                self._web_search_calls_used = max(0, self._web_search_calls_used - 1)

    def _reserve_external_text_budget(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> int:
        cap = self.policy.max_single_fetch_chars
        total = self.policy.max_external_text_chars_per_turn
        if total is not None:
            remaining = (
                total
                - self._external_text_chars_used
                - self._external_text_chars_reserved
            )
            if remaining <= 0:
                raise ToolRunBudgetExceededError(
                    tool_name,
                    f"Tool '{tool_name}' exceeded the external text run budget ({total}).",
                )
            cap = remaining if cap is None else min(cap, remaining)
        if cap is None:
            return 0
        cap = max(0, int(cap))
        if tool_name == "web_fetch":
            if cap < WEB_FETCH_MIN_MAX_CHARS:
                raise ToolRunBudgetExceededError(
                    tool_name,
                    (
                        "web_fetch cannot enforce the remaining run budget below "
                        f"{WEB_FETCH_MIN_MAX_CHARS} characters."
                    ),
                )
            requested = arguments.get("max_chars")
            try:
                requested_int = int(requested) if requested is not None else None
            except (TypeError, ValueError):
                requested_int = None
            if requested_int is None or requested_int > cap:
                arguments["max_chars"] = cap
        self._external_text_chars_reserved += cap
        return cap

    def _release_external_reservation(
        self,
        reservation: ToolRunBudgetReservation,
    ) -> None:
        if reservation.reserved_external_text_chars:
            self._external_text_chars_reserved = max(
                0,
                self._external_text_chars_reserved
                - reservation.reserved_external_text_chars,
            )

    @staticmethod
    def _check_call_budget(
        *,
        tool_name: str,
        used: int,
        limit: int | None,
    ) -> None:
        if limit is not None and used >= limit:
            raise ToolRunBudgetExceededError(
                tool_name,
                f"Tool '{tool_name}' exceeded the per-turn call budget ({limit}).",
            )


@dataclass(frozen=True)
class ToolResultBudgetDecision:
    content: str
    changed: bool
    original_chars: int
    returned_chars: int
    budget_class: ToolResultBudgetClass


class ToolResultBudgetTracker:
    """Concurrency-safe per-turn accounting for normalized result previews."""

    def __init__(self, policy: ToolResultBudgetPolicy | None = None) -> None:
        self.policy = policy or DEFAULT_TOOL_RESULT_BUDGET_POLICY
        self._lock = asyncio.Lock()
        self._tool_chars_used = 0
        self._external_chars_used = 0

    async def normalize(
        self,
        *,
        tool_name: str,
        content: str,
        budget_class: ToolResultBudgetClass,
        is_error: bool = False,
    ) -> ToolResultBudgetDecision:
        if not isinstance(content, str):
            content = str(content)
        if is_error and budget_class is not ToolResultBudgetClass.CONTROL:
            budget_class = ToolResultBudgetClass.ERROR
        if budget_class is ToolResultBudgetClass.ARTIFACT:
            return ToolResultBudgetDecision(
                content=content,
                changed=False,
                original_chars=len(content),
                returned_chars=len(content),
                budget_class=budget_class,
            )
        if budget_class is ToolResultBudgetClass.CONTROL:
            single_limit = self.policy.max_single_tool_result_chars
        elif budget_class is ToolResultBudgetClass.EXTERNAL:
            single_limit = self.policy.max_single_external_result_chars
        else:
            single_limit = self.policy.max_single_tool_result_chars

        original_chars = len(content)
        async with self._lock:
            remaining_total = max(
                0,
                self.policy.max_tool_result_chars_per_turn - self._tool_chars_used,
            )
            remaining_external = (
                max(
                    0,
                    self.policy.max_external_tool_result_chars_per_turn
                    - self._external_chars_used,
                )
                if budget_class is ToolResultBudgetClass.EXTERNAL
                else remaining_total
            )
            allowed = max(0, min(single_limit, remaining_total, remaining_external))
            if budget_class is ToolResultBudgetClass.ERROR:
                allowed = max(allowed, min(single_limit, self.policy.min_error_result_chars))
            elif budget_class is ToolResultBudgetClass.CONTROL:
                allowed = max(
                    allowed,
                    min(single_limit, self.policy.min_control_result_chars),
                )
            if original_chars <= allowed:
                self._tool_chars_used += original_chars
                if budget_class is ToolResultBudgetClass.EXTERNAL:
                    self._external_chars_used += original_chars
                return ToolResultBudgetDecision(
                    content=content,
                    changed=False,
                    original_chars=original_chars,
                    returned_chars=original_chars,
                    budget_class=budget_class,
                )

            compacted = compact_tool_result_content(
                tool_name=tool_name,
                content=content,
                max_preview_chars=allowed,
                budget_class=budget_class,
                is_error=is_error,
            )
            returned_chars = _preview_chars(compacted)
            self._tool_chars_used += returned_chars
            if budget_class is ToolResultBudgetClass.EXTERNAL:
                self._external_chars_used += returned_chars
            return ToolResultBudgetDecision(
                content=compacted,
                changed=True,
                original_chars=original_chars,
                returned_chars=returned_chars,
                budget_class=budget_class,
            )


def resolve_budget_class(tool_name: str, explicit: Any = None) -> ToolResultBudgetClass:
    if isinstance(explicit, ToolResultBudgetClass):
        return explicit
    if isinstance(explicit, str):
        try:
            return ToolResultBudgetClass(explicit)
        except ValueError:
            pass
    if tool_name in CONTROL_TOOL_NAMES:
        return ToolResultBudgetClass.CONTROL
    if tool_name in EXTERNAL_TOOL_NAMES:
        return ToolResultBudgetClass.EXTERNAL
    return ToolResultBudgetClass.UNKNOWN


def clamp_tool_arguments(
    tool_name: str,
    arguments: dict[str, Any],
    policy: ToolResultBudgetPolicy,
) -> dict[str, Any]:
    next_args = dict(arguments)
    if tool_name == "web_fetch":
        requested = next_args.get("max_chars")
        if isinstance(requested, int):
            next_args["max_chars"] = min(max(100, requested), policy.max_web_fetch_chars)
        elif requested is None:
            next_args["max_chars"] = policy.max_web_fetch_chars
    elif tool_name == "web_search":
        requested = next_args.get("max_results")
        if isinstance(requested, int):
            next_args["max_results"] = min(max(1, requested), policy.max_web_search_results)
        elif requested is None:
            next_args["max_results"] = policy.max_web_search_results
    return next_args


def compact_tool_result_content(
    *,
    tool_name: str,
    content: str,
    max_preview_chars: int,
    budget_class: ToolResultBudgetClass,
    is_error: bool = False,
) -> str:
    max_preview_chars = max(0, max_preview_chars)
    original_chars = len(content)
    if budget_class is ToolResultBudgetClass.CONTROL:
        return _compact_control_json(
            tool_name=tool_name,
            content=content,
            max_preview_chars=max_preview_chars,
            original_chars=original_chars,
            budget_class=budget_class,
        )
    preview = content[:max_preview_chars]
    payload: dict[str, Any] = {
        "tool_result_budget_applied": True,
        "result_truncated": True,
        "result_original_chars": original_chars,
        "result_returned_chars": len(preview),
        "budget_class": budget_class.value,
        "tool": tool_name,
        "is_error": bool(is_error),
        "preview": preview,
    }
    return json.dumps(payload, ensure_ascii=False)


def _compact_control_json(
    *,
    tool_name: str,
    content: str,
    max_preview_chars: int,
    original_chars: int,
    budget_class: ToolResultBudgetClass,
) -> str:
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        preview = content[:max_preview_chars]
        return json.dumps(
            {
                "tool_result_budget_applied": True,
                "result_truncated": True,
                "result_original_chars": original_chars,
                "result_returned_chars": len(preview),
                "budget_class": budget_class.value,
                "tool": tool_name,
                "preview": preview,
            },
            ensure_ascii=False,
        )
    if not isinstance(payload, dict):
        preview = content[:max_preview_chars]
        return json.dumps(
            {
                "tool_result_budget_applied": True,
                "result_truncated": True,
                "result_original_chars": original_chars,
                "result_returned_chars": len(preview),
                "budget_class": budget_class.value,
                "tool": tool_name,
                "preview": preview,
            },
            ensure_ascii=False,
        )

    compacted = dict(payload)
    for key, value in list(compacted.items()):
        if isinstance(value, str) and len(value) > max_preview_chars:
            compacted[key] = value[:max_preview_chars]
    compacted["tool_result_budget_applied"] = True
    compacted["result_truncated"] = True
    compacted["result_original_chars"] = original_chars
    compacted["result_returned_chars"] = _string_value_chars(compacted)
    compacted["budget_class"] = budget_class.value
    compacted["tool"] = tool_name
    return json.dumps(compacted, ensure_ascii=False)


def _preview_chars(rendered: str) -> int:
    try:
        payload = json.loads(rendered)
    except (TypeError, ValueError):
        return len(rendered)
    if isinstance(payload, dict):
        value = payload.get("result_returned_chars")
        if isinstance(value, int):
            return value
    return len(rendered)


def _string_value_chars(payload: dict[str, Any]) -> int:
    total = 0
    for value in payload.values():
        if isinstance(value, str):
            total += len(value)
    return total
