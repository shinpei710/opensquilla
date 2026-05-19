"""Derive a structured OnboardingStatus from a GatewayConfig.

The per-section truth lives in :mod:`opensquilla.onboarding.section_status`;
this module composes those verifiers, computes the legacy boolean view
required by WebUI RPC and ``next_steps``, and exposes ``llm_source`` /
``image_generation_*`` annotations that the CLI status renderers need.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from opensquilla.gateway.config import GatewayConfig
from opensquilla.onboarding.config_store import default_config_path
from opensquilla.onboarding.image_generation_specs import (
    get_image_generation_provider_setup_spec,
    list_image_generation_provider_setup_specs,
)
from opensquilla.onboarding.provider_specs import get_provider_setup_spec
from opensquilla.onboarding.section_status import (
    SectionStatus,
    channels_section_status,
    image_generation_section_status,
    llm_section_status,
    router_section_status,
    search_section_status,
    section_verifiers,
)
from opensquilla.onboarding.section_status import (
    needs_onboarding as _needs_onboarding,
)


@dataclass(frozen=True)
class OnboardingStatus:
    config_path: str | None
    has_config: bool
    llm_configured: bool
    llm_source: str
    image_generation_configured: bool
    image_generation_enabled: bool
    image_generation_source: str
    image_generation_provider: str
    image_generation_primary: str
    search_configured: bool
    channel_count: int
    channels_configured: bool
    needs_onboarding: bool
    sections: dict[str, SectionStatus] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


def _llm_source(cfg: GatewayConfig, status: SectionStatus) -> str:
    """Re-derive the legacy ``llm_source`` annotation alongside the verifier.

    The verifier collapses the source detail into a single enum so it stays
    composable with the other sections; this helper keeps the existing
    ``"explicit" / "env" / "missing_env" / "none"`` annotation alive for the
    CLI/WebUI renderers that already display it.
    """
    llm = cfg.llm
    if not llm.provider or not llm.model:
        return "none"
    try:
        spec = get_provider_setup_spec(llm.provider)
    except KeyError:
        return "none"
    if not spec.runtime_supported or not spec.requires_api_key:
        return "none"
    if status is SectionStatus.OK and llm.api_key and (
        "llm.api_key" not in getattr(cfg, "_runtime_secret_paths", set())
    ):
        return "explicit"
    env_key = (getattr(llm, "api_key_env", "") or "").strip()
    if env_key and os.environ.get(env_key):
        return "env"
    if env_key:
        return "missing_env"
    return "none"


def _image_generation_provider_config(cfg: GatewayConfig, provider_id: str) -> object | None:
    providers = getattr(getattr(cfg, "image_generation", None), "providers", None)
    return getattr(providers, provider_id, None) if providers is not None else None


def _image_generation_provider_source(
    cfg: GatewayConfig,
    provider_id: str,
) -> tuple[str, str]:
    try:
        spec = get_image_generation_provider_setup_spec(provider_id)
    except KeyError:
        return "", ""

    provider_cfg = _image_generation_provider_config(cfg, provider_id)
    explicit_key = getattr(provider_cfg, "api_key", "") if provider_cfg else ""
    if explicit_key:
        return "explicit", spec.env_key

    env_key = getattr(provider_cfg, "api_key_env", spec.env_key) if provider_cfg else spec.env_key
    if env_key and os.environ.get(env_key):
        return "env", env_key

    llm = getattr(cfg, "llm", None)
    if getattr(llm, "provider", "").strip().lower() == provider_id and getattr(llm, "api_key", ""):
        return "llm_fallback", spec.env_key
    return "", spec.env_key


def _configured_image_generation_provider_ids(cfg: GatewayConfig) -> list[str]:
    image_cfg = cfg.image_generation
    refs: list[str] = []
    primary = getattr(image_cfg, "primary", "")
    fallbacks = list(getattr(image_cfg, "fallbacks", []) or [])
    default_primary = "openai/gpt-image-1"
    explicit_model_routing = bool(fallbacks) or bool(primary and primary != default_primary)
    if explicit_model_routing:
        refs = [primary, *fallbacks]
    else:
        refs = [
            spec.default_model
            for spec in list_image_generation_provider_setup_specs()
            if spec.runtime_supported
        ]

    provider_ids: list[str] = []
    seen: set[str] = set()
    for ref in refs:
        provider_id, sep, _model = ref.partition("/")
        provider_id = provider_id.strip()
        if sep and provider_id and provider_id not in seen:
            seen.add(provider_id)
            provider_ids.append(provider_id)
    return provider_ids


def _image_generation_annotations(
    cfg: GatewayConfig,
    status: SectionStatus,
) -> tuple[str, str, str]:
    image_cfg = cfg.image_generation
    primary = getattr(image_cfg, "primary", "")
    if status is SectionStatus.OPTIONAL:
        return "none", "", primary
    for provider_id in _configured_image_generation_provider_ids(cfg):
        source, _env_key = _image_generation_provider_source(cfg, provider_id)
        if source:
            return source, provider_id, primary
    return "none", "", primary


def get_onboarding_status(config: GatewayConfig) -> OnboardingStatus:
    path = Path(config.config_path).expanduser() if config.config_path else default_config_path()
    has_config = path.exists()

    sections = {name: verifier(config) for name, verifier in section_verifiers().items()}

    llm_status = sections["llm"]
    image_status = sections["image_generation"]
    image_source, image_provider, image_primary = _image_generation_annotations(
        config, image_status
    )

    enabled_channels = [c for c in config.channels.channels if c.enabled]

    return OnboardingStatus(
        config_path=str(path),
        has_config=has_config,
        llm_configured=llm_status is SectionStatus.OK,
        llm_source=_llm_source(config, llm_status),
        image_generation_configured=image_status is SectionStatus.OK,
        image_generation_enabled=bool(getattr(config.image_generation, "enabled", False)),
        image_generation_source=image_source,
        image_generation_provider=image_provider,
        image_generation_primary=image_primary,
        search_configured=sections["search"] is SectionStatus.OK,
        channel_count=len(config.channels.channels),
        channels_configured=bool(enabled_channels),
        needs_onboarding=_needs_onboarding(sections),
        sections=sections,
    )


__all__ = [
    "OnboardingStatus",
    "SectionStatus",
    "get_onboarding_status",
    "channels_section_status",
    "image_generation_section_status",
    "llm_section_status",
    "router_section_status",
    "search_section_status",
]
