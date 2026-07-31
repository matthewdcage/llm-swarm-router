"""F-36: detect drift between the pinned OpenAI SDK signatures and our mirrors.

`_SDK_CHAT_PARAMS` / `_SDK_EMBEDDINGS_PARAMS` are hand-maintained copies of the
pinned SDK method signatures. An SDK bump that adds or removes typed params
would otherwise silently break the extension-field split (TypeError -> 502s, or
new typed params detouring through extra_body). These asserts fail loudly on
the bump instead.
"""

from __future__ import annotations

import inspect

from netllm_sdk_openai.payload import (
    _SDK_CHAT_PARAMS,
    _SDK_CONTROL_PARAMS,
    _SDK_EMBEDDINGS_PARAMS,
)
from openai.resources.chat.completions import AsyncCompletions
from openai.resources.embeddings import AsyncEmbeddings


def test_chat_params_match_pinned_sdk_signature() -> None:
    sig_params = set(inspect.signature(AsyncCompletions.create).parameters) - {"self"}
    # Control kwargs (F-42) are deliberately excluded from the settable set.
    assert sig_params == _SDK_CHAT_PARAMS | _SDK_CONTROL_PARAMS


def test_embeddings_params_match_pinned_sdk_signature() -> None:
    sig_params = set(inspect.signature(AsyncEmbeddings.create).parameters) - {"self"}
    assert sig_params == _SDK_EMBEDDINGS_PARAMS | _SDK_CONTROL_PARAMS


def test_control_params_are_disjoint_from_settable_sets() -> None:
    assert not _SDK_CONTROL_PARAMS & _SDK_CHAT_PARAMS
    assert not _SDK_CONTROL_PARAMS & _SDK_EMBEDDINGS_PARAMS
