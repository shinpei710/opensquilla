"""Helpers for normalizing user-supplied secrets."""

from __future__ import annotations

_TRAILING_PASTE_PUNCTUATION = "、，。；;：:,. \t\r\n"


def clean_header_secret(value: str | None, *, label: str = "API key") -> str:
    """Return a secret safe to place in an HTTP header value.

    API keys are often pasted from chat or docs. On Windows terminals it is easy
    to carry a trailing full-width punctuation mark into the password prompt;
    strip those boundary characters while rejecting non-ASCII text that remains.
    """

    cleaned = str(value or "").strip().rstrip(_TRAILING_PASTE_PUNCTUATION)
    try:
        cleaned.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError(
            f"{label} contains non-ASCII characters; remove copied punctuation "
            "and paste the key again."
        ) from exc
    return cleaned
