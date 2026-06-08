"""Tests for vendor SDK version payload helpers."""

from __future__ import annotations

from netllm_core.sdk_versions import sdk_versions_payload


def test_sdk_versions_payload_includes_vendor_packages() -> None:
    payload = sdk_versions_payload()
    assert payload["openai"]
    assert payload["anthropic"]
