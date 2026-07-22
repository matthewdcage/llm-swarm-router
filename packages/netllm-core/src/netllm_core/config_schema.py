"""Generic UI-form schema for the 6 editable NetllmConfig sections.

Walks the pydantic models (not an instance — this is shape, not values;
see admin.config_summary for values) and emits one document a client can
render generic forms from instead of hand-mirroring the shape in Swift
and JS. See docs/config-schema-rewrite-plan.md.

Widget/secrecy/read-only hints come from each field's
`Field(json_schema_extra={...})` — see the "widget", "write_only",
"read_only", "group", "options_from" keys used across models.py. Fields
without explicit hints get a widget inferred from their Python type.
"""

from __future__ import annotations

import types
import typing
from typing import Any, Literal, get_args, get_origin

from pydantic import BaseModel
from pydantic.fields import FieldInfo

from netllm_core.models import (
    AgentConfig,
    CloudConfig,
    DiscoveryLocalConfig,
    DiscoverySwarmConfig,
    RoutingConfig,
    UiConfig,
)
from netllm_core.version import get_version

# Section key (matches NetllmConfig's own field names) -> root model.
SECTIONS: dict[str, type[BaseModel]] = {
    "agent": AgentConfig,
    "discovery": DiscoveryLocalConfig,
    "swarm": DiscoverySwarmConfig,
    "routing": RoutingConfig,
    "ui": UiConfig,
    "cloud": CloudConfig,
}

# Minimal section list a client can fall back to against an older agent
# that predates this endpoint (see plan §4) — enough to reach a running
# agent's other admin routes.
BOOTSTRAP_SECTIONS = ("agent", "discovery")


def _unwrap_optional(annotation: Any) -> tuple[Any, bool]:
    """`X | None` -> (X, True); anything else -> (annotation, False)."""
    origin = get_origin(annotation)
    if origin is typing.Union or origin is types.UnionType:
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0], True
    return annotation, False


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


def _field_default(field: FieldInfo, *, read_only: bool) -> Any:
    # Per-instance-random read-only defaults (agent_id's uuid4, hostname's
    # platform lookup) would make the schema document nondeterministic
    # between calls, defeating the version/ETag caching contract (§3.2 of
    # the plan) — the real value belongs to the values endpoint
    # (admin.config_summary), not the shape endpoint.
    if read_only or field.is_required():
        return None
    return _jsonable(field.get_default(call_default_factory=True))


def _field_spec(name: str, field: FieldInfo) -> dict[str, Any]:
    annotation, optional = _unwrap_optional(field.annotation)
    extra = field.json_schema_extra if isinstance(field.json_schema_extra, dict) else {}
    origin = get_origin(annotation)
    read_only = bool(extra.get("read_only"))

    spec: dict[str, Any] = {"name": name, "default": _field_default(field, read_only=read_only)}
    if optional:
        spec["optional"] = True
    if field.description:
        spec["help"] = field.description

    if origin is Literal:
        spec["type"] = "string"
        spec["widget"] = extra.get("widget", "select")
        spec["options"] = list(get_args(annotation))
    elif annotation is bool:
        spec["type"] = "boolean"
        spec["widget"] = extra.get("widget", "toggle")
    elif annotation in (int, float):
        spec["type"] = "number"
        spec["widget"] = extra.get("widget", "number")
    elif annotation is str:
        spec["type"] = "string"
        spec["widget"] = extra.get("widget", "text")
    elif origin is list and get_args(annotation):
        item_type = get_args(annotation)[0]
        if isinstance(item_type, type) and issubclass(item_type, BaseModel):
            spec["type"] = "array"
            spec["widget"] = "list"
            spec["item_schema"] = _model_field_specs(item_type)
        else:
            spec["type"] = "array"
            spec["widget"] = extra.get("widget", "list_strings")
    elif origin is dict and get_args(annotation):
        _key_type, value_type = get_args(annotation)
        value_origin = get_origin(value_type)
        if isinstance(value_type, type) and issubclass(value_type, BaseModel):
            spec["type"] = "object"
            spec["widget"] = "dict"
            spec["item_schema"] = _model_field_specs(value_type)
        elif value_origin is list:
            spec["type"] = "object"
            spec["widget"] = extra.get("widget", "dict_list_strings")
        else:
            spec["type"] = "object"
            spec["widget"] = extra.get("widget", "dict")
    else:
        # Fallback for any type not modeled above (e.g. a bare object) —
        # renders as read-only text rather than silently vanishing.
        spec["type"] = "string"
        spec["widget"] = extra.get("widget", "text")

    if extra.get("write_only"):
        spec["write_only"] = True
    if extra.get("read_only"):
        spec["read_only"] = True
    if "group" in extra:
        spec["group"] = extra["group"]
    if "options_from" in extra:
        spec["options_from"] = extra["options_from"]
    if "default_factory" in extra:
        spec["default_factory"] = extra["default_factory"]

    return spec


def _model_field_specs(model: type[BaseModel]) -> list[dict[str, Any]]:
    return [_field_spec(name, field) for name, field in model.model_fields.items()]


def config_schema_document() -> dict[str, Any]:
    """The full schema document served at GET /netllm/v1/config/schema."""
    return {
        "version": get_version(),
        "sections": {
            key: {"fields": _model_field_specs(model)}
            for key, model in SECTIONS.items()
        },
    }
