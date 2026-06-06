"""OpenAI SDK adapter — all upstream OpenAI-compat calls go through here."""

from netllm_sdk_openai.client import OpenAIUpstream, OpenAIUpstreamError

__all__ = ["OpenAIUpstream", "OpenAIUpstreamError"]
