"""Every widget the config schema emits has a real control on both UIs.

The macOS schema form used to send any widget it did not recognise to a plain
text field whose binding wrote a string. For a structured field (a source's
``model_rewrites`` / ``scenarios`` / ``match``) that replaced the dict the
moment the user typed in it, while the presence-by-substring parity checks
stayed green (audit F-144). This asserts renderers by widget name instead.
"""

from __future__ import annotations

import re
from pathlib import Path

from netllm_core.config_schema import config_schema_document

ROOT = Path(__file__).resolve().parents[1]
SWIFT_FORM = ROOT / "apps/netllm-mac/Sources/AppView/SchemaFormView.swift"
WEB_FORM = ROOT / "packages/netllm-agent/src/netllm_agent/static/schema-form.js"
STRUCTURED = {"dict_strings", "dict_list_strings", "dict", "list", "object"}


def _schema_widgets() -> set[str]:
    found: set[str] = set()

    def walk(fields: list[dict]) -> None:
        for field in fields:
            found.add(field["widget"])
            walk(field.get("item_schema") or [])

    for section in config_schema_document()["sections"].values():
        walk(section["fields"])
    return found


def _swift_row_switch() -> str:
    text = SWIFT_FORM.read_text()
    start = text.index("switch field.widget {")
    return text[start : text.index("private var valueBinding", start)]


def test_schema_emits_the_structured_widgets_this_guards() -> None:
    assert STRUCTURED <= _schema_widgets()


def test_macos_form_has_a_control_for_every_schema_widget() -> None:
    handled = set(re.findall(r'case "([a-z_]+)":', _swift_row_switch()))
    assert _schema_widgets() - handled == set()


def test_macos_form_never_falls_back_to_a_text_field() -> None:
    default_branch = _swift_row_switch().split("default:", 1)[1]
    assert "TextField" not in default_branch
    assert ".string(" not in default_branch


def test_dashboard_form_has_a_control_for_every_schema_widget() -> None:
    handled = set(re.findall(r'field\.widget === "([a-z_]+)"', WEB_FORM.read_text()))
    # "text" is the dashboard's documented fallthrough; a string input is the
    # right control for it and only for it.
    assert _schema_widgets() - handled - {"text"} == set()
