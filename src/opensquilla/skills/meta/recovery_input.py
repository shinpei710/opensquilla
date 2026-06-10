"""Shared validation for awaiting MetaSkill user-input recovery.

Both operator surfaces and the CLI need the same conservative contract:
parse the persisted awaiting schema, merge any already-stored partial fields,
coerce submitted JSON values through the clarify validators, and report exactly
what is filled or missing without claiming the run.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from opensquilla.skills.meta.clarify_schema import schema_to_protocol
from opensquilla.skills.meta.clarify_text import _coerce_and_validate
from opensquilla.skills.meta.plan_serde import clarify_config_from_jsonable
from opensquilla.skills.meta.types import ClarifyStepConfig


@dataclass(frozen=True)
class ResumeInputValidation:
    schema: ClarifyStepConfig
    schema_dict: dict[str, Any]
    submitted_fields: dict[str, Any]
    stored_fields: dict[str, Any]
    filled_fields: dict[str, Any]
    missing_fields: list[str]
    validation_errors: list[str]

    @property
    def ok(self) -> bool:
        return not self.validation_errors


def json_object_or_errors(
    raw: str | None,
    *,
    label: str,
) -> tuple[dict[str, Any], list[str]]:
    if raw is None or not raw.strip():
        return {}, []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {}, [f"{label} is not valid JSON: {exc}"]
    if not isinstance(parsed, dict):
        return {}, [f"{label} must be a JSON object, got {type(parsed).__name__}"]
    return parsed, []


def validate_resume_input(
    *,
    awaiting_schema_json: str,
    awaiting_filled_json: str,
    submitted_fields: Mapping[str, Any] | None,
) -> ResumeInputValidation:
    schema_dict, schema_errors = json_object_or_errors(
        awaiting_schema_json,
        label="awaiting_schema_json",
    )
    stored_fields, stored_errors = json_object_or_errors(
        awaiting_filled_json,
        label="awaiting_filled_json",
    )
    stored_fields.pop("__prefill_audit__", None)

    submitted = dict(submitted_fields or {})
    parse_errors = schema_errors + stored_errors

    schema: ClarifyStepConfig | None = None
    if not schema_errors:
        try:
            schema = clarify_config_from_jsonable(schema_dict)
        except Exception as exc:  # noqa: BLE001
            parse_errors.append(f"awaiting_schema_json is not a valid schema: {exc}")
    if schema is None:
        schema = ClarifyStepConfig(mode="form", fields=())

    filled_fields: dict[str, Any] = {}
    missing_fields: list[str] = []
    validation_errors: list[str] = []
    if not parse_errors:
        filled_fields, validation_errors, missing_fields = _validate_resume_fields(
            schema=schema,
            stored_fields=stored_fields,
            submitted_fields=submitted,
        )

    return ResumeInputValidation(
        schema=schema,
        schema_dict=schema_dict,
        submitted_fields=submitted,
        stored_fields=stored_fields,
        filled_fields=filled_fields,
        missing_fields=missing_fields,
        validation_errors=parse_errors + validation_errors,
    )


def validate_resume_input_json(
    *,
    awaiting_schema_json: str,
    awaiting_filled_json: str,
    fields_json: str | None,
    label: str = "--fields-json",
) -> ResumeInputValidation:
    submitted, submitted_errors = json_object_or_errors(fields_json, label=label)
    validation = validate_resume_input(
        awaiting_schema_json=awaiting_schema_json,
        awaiting_filled_json=awaiting_filled_json,
        submitted_fields=submitted,
    )
    if not submitted_errors:
        return validation
    return ResumeInputValidation(
        schema=validation.schema,
        schema_dict=validation.schema_dict,
        submitted_fields=submitted,
        stored_fields=validation.stored_fields,
        filled_fields=validation.filled_fields,
        missing_fields=validation.missing_fields,
        validation_errors=submitted_errors + validation.validation_errors,
    )


def schema_field_names(schema: ClarifyStepConfig) -> list[str]:
    return [str(field.name) for field in schema.fields]


def schema_required_field_names(schema: ClarifyStepConfig) -> list[str]:
    return [
        str(field.name)
        for field in schema.fields
        if bool(getattr(field, "required", False))
    ]


def resume_input_protocol_payload(
    *,
    awaiting: Any,
    validation: ResumeInputValidation,
    gateway_required: bool = True,
) -> dict[str, Any]:
    return {
        "awaiting_step_id": awaiting.step_id,
        "awaiting_session_id": awaiting.awaiting_session_id,
        "field_names": schema_field_names(validation.schema),
        "required_fields": schema_required_field_names(validation.schema),
        "submitted_fields": validation.submitted_fields,
        "stored_fields": validation.stored_fields,
        "filled_fields": validation.filled_fields,
        "missing_fields": validation.missing_fields,
        "validation_errors": validation.validation_errors,
        "schema": schema_to_protocol(validation.schema),
        "gateway_required": gateway_required,
    }


def clarify_value_to_string(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value)


def _validate_resume_fields(
    *,
    schema: ClarifyStepConfig,
    stored_fields: dict[str, Any],
    submitted_fields: dict[str, Any],
) -> tuple[dict[str, Any], list[str], list[str]]:
    field_by_name = {str(field.name): field for field in schema.fields}
    known_stored = {
        name: value
        for name, value in stored_fields.items()
        if name in field_by_name
    }
    combined_raw = {**known_stored, **submitted_fields}
    errors: list[str] = []
    filled: dict[str, Any] = {}

    for name in submitted_fields:
        if name not in field_by_name:
            errors.append(
                f"unknown field {name!r}; valid fields: "
                f"{', '.join(field_by_name)}",
            )

    for name, raw_value in combined_raw.items():
        field = field_by_name.get(name)
        if field is None:
            continue
        coerced, field_errors = _coerce_and_validate(
            field,
            clarify_value_to_string(raw_value),
        )
        if field_errors:
            errors.extend(field_errors)
        else:
            filled[name] = coerced

    missing = [
        str(field.name)
        for field in schema.fields
        if bool(getattr(field, "required", False)) and field.name not in filled
    ]
    for name in missing:
        errors.append(f"required field {name!r}: no value provided")

    if errors:
        return filled, errors, missing
    return filled, [], []
