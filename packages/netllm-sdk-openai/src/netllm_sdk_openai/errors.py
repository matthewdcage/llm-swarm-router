"""Shared exceptions for the OpenAI SDK adapter package."""

from __future__ import annotations


class OpenAIUpstreamError(Exception):
    """Normalized upstream failure."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
