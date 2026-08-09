"""OpenAI SDK adapter — all upstream OpenAI-compat calls go through here."""

from netllm_sdk_openai.client import OpenAIUpstream
from netllm_sdk_openai.errors import OpenAIUpstreamError

__all__ = ["OpenAIUpstream", "OpenAIUpstreamError"]
