"""Cloud credential verification — one answer to "can this key actually work?"

The Cloud page used to let a provider be switched on with no credential at
all and then presented it exactly like a working one. The user believes they
have failover; the first request that cannot be served locally fails instead
of falling through. A silent non-functional safety net is worse than none, so
enablement has to be *earned*: a credential is checked against the provider
before the provider can be turned on.

Three things live here rather than in the agent, and all three are the reason
this module is in ``netllm-core`` at all:

1. **The probe.** ``probe_cloud_provider`` is the only live check. Both
   writers need it — the agent serves it over HTTP for the two GUIs, and
   ``netllm cloud enable`` runs it in-process before it writes config, because
   the CLI can reach the network and a config guard cannot.
2. **The record.** The outcome is persisted onto ``CloudProviderConfig`` as
   four read-only fields, so it survives a page reload *and* a restart *and*
   is visible to the CLI process, which shares no runtime state with a running
   agent. Runtime state would have failed the third of those, and the third is
   the one the write-path gate depends on.
3. **The vocabulary.** ``verification_state`` renders the specific blocker —
   *no key set*, *key rejected (401)*, *endpoint unreachable*, *never
   checked* — as a sentence the server owns. Every surface prints the server's
   sentence instead of re-deriving one, which is the same anti-mirror rule
   ``cloud_provider_registry_payload`` follows for display metadata.

**The key itself is never stored here and never returned.** ``probe_cloud_
provider`` accepts an unsaved key so a user can check a pasted credential
without first saving a broken one; what is written down is
``key_fingerprint`` — a truncated SHA-256 — which is what lets a later save of
that same key be recognised as verified, and a *different* key be recognised
as unverified.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import httpx

from netllm_core.cloud_providers import CloudProviderSpec
from netllm_core.models import CloudProviderConfig

# --- status vocabulary ----------------------------------------------------
#
# Deliberately finer-grained than health.py's backend statuses: "error" is
# what the Backends page can afford to say about a box on the LAN, and it is
# not what a user needs to be told about a credential they just pasted.

STATUS_OK = "ok"
STATUS_NO_KEY = "no_key"
STATUS_UNAUTHORIZED = "unauthorized"
STATUS_NO_ENDPOINT = "no_endpoint"
STATUS_UNREACHABLE = "unreachable"
STATUS_TIMEOUT = "timeout"
STATUS_INCONCLUSIVE = "inconclusive"
STATUS_ERROR = "error"
#: Stored-record-only statuses: never produced by a probe.
STATUS_NEVER_CHECKED = "never_checked"
STATUS_KEY_CHANGED = "key_changed"

#: Positive proof the credential cannot work. Everything else — a refused
#: connection, a timeout, a provider with no catalogue API — is *absence* of
#: proof, and absence of proof must not disable a user's failover. The gate
#: fails open on ambiguity and closed only on these.
CONCLUSIVE_FAILURES = frozenset(
    {STATUS_NO_KEY, STATUS_UNAUTHORIZED, STATUS_NO_ENDPOINT}
)

#: Fields ``record_verification`` writes. Server-owned: they are read_only in
#: the schema and excluded from the config-merge allowlist, so no client patch
#: can forge a passing record and walk through the gate.
VERIFICATION_FIELDS: tuple[str, ...] = (
    "verified_status",
    "verified_at",
    "verified_detail",
    "verified_key_fingerprint",
)

DEFAULT_TIMEOUT_S = 10.0


def _now() -> str:
    return datetime.now(UTC).isoformat()


def key_fingerprint(api_key: str) -> str:
    """Stable, non-reversing tag for a credential.

    Truncated SHA-256 of a high-entropy secret: enough to answer "is this the
    same key the check passed with?" — which is the whole question — without
    storing anything that could be replayed. Empty in, empty out, so a
    keyless provider never collides with a keyed one.
    """
    key = (api_key or "").strip()
    if not key:
        return ""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def resolve_api_key(
    provider_cfg: CloudProviderConfig | None,
    spec: CloudProviderSpec,
    env: Mapping[str, str] | None = None,
) -> str:
    """The credential the agent would actually send, by the same precedence
    ``_materialize_cloud_provider_backends`` uses.

    Inline key, then the entry's own ``api_key_env``, then the registry's
    default env var, then the plan-token variable for ``auth = "plan_token"``.
    Written once here because it was written three times (materialize, the
    catalogue probe, the CLI's ``cloud test``) and a fourth copy is how the
    UI's idea of "has a key" drifts from the router's.
    """
    environ = os.environ if env is None else env
    cfg = provider_cfg
    key = ""
    if cfg is not None:
        key = cfg.api_key or (
            environ.get(cfg.api_key_env, "") if cfg.api_key_env else ""
        )
    key = key or environ.get(spec.api_key_env, "")
    if not key and cfg is not None and cfg.auth == "plan_token":
        key = environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
    return key


def resolve_endpoint(
    provider_cfg: CloudProviderConfig | None, spec: CloudProviderSpec
) -> tuple[str, str]:
    """``(base_url, api_format)`` the probe should talk to — empty base_url
    when the chosen region/format combination has no endpoint at all."""
    cfg = provider_cfg
    api_format = (cfg.api_format if cfg else None) or spec.default_api_format
    endpoint = spec.endpoint((cfg.region or None) if cfg else None)
    base_url = (cfg.base_url if cfg else "") or (
        endpoint.anthropic_base_url
        if api_format == "anthropic"
        else endpoint.openai_base_url
    )
    return (base_url or "", api_format)


def blocker_sentence(status: str, detail: str, spec: CloudProviderSpec) -> str:
    """The one line a provider row carries when it cannot be enabled.

    Specific by construction: the caller never has to choose between "an
    error occurred" and inventing its own wording, which is how the Cloud
    page ended up saying nothing useful while the Backends page said exactly
    what a probe found.
    """
    tail = f" {detail}" if detail else ""
    if status == STATUS_OK:
        return ""
    if status == STATUS_NO_KEY:
        return (
            f"No key set — paste one below, or export {spec.api_key_env} "
            "for the agent process."
        )
    if status == STATUS_UNAUTHORIZED:
        return f"Key rejected by {spec.display_name}.{tail}"
    if status == STATUS_NO_ENDPOINT:
        return f"No endpoint for this region and API format.{tail}"
    if status == STATUS_UNREACHABLE:
        return f"{spec.display_name} is unreachable from this agent.{tail}"
    if status == STATUS_TIMEOUT:
        return f"{spec.display_name} did not answer in time.{tail}"
    if status == STATUS_NEVER_CHECKED:
        return "Never checked — verify the key before enabling this provider."
    if status == STATUS_KEY_CHANGED:
        return "The key changed since the last check — verify it again."
    if status == STATUS_INCONCLUSIVE:
        return f"The check could not confirm this key.{tail}"
    return f"The check failed.{tail}"


def verification_state(
    provider_cfg: CloudProviderConfig | None,
    spec: CloudProviderSpec,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """The wire projection every surface renders.

    ``status`` folds the stored record together with the credential that is
    configured *now*: a record that passed against a key which has since been
    replaced reads ``key_changed``, not ``ok``, because it no longer describes
    the credential the router would send.
    """
    cfg = provider_cfg
    api_key = resolve_api_key(cfg, spec, env)
    fingerprint = key_fingerprint(api_key)
    stored_status = (cfg.verified_status if cfg else "") or ""
    stored_fingerprint = (cfg.verified_key_fingerprint if cfg else "") or ""
    detail = (cfg.verified_detail if cfg else "") or ""
    checked_at = (cfg.verified_at if cfg else "") or ""

    stale = bool(stored_status) and stored_fingerprint != fingerprint
    if not api_key:
        status = STATUS_NO_KEY
        detail = ""
    elif not stored_status:
        status = STATUS_NEVER_CHECKED
        detail = ""
    elif stale:
        status = STATUS_KEY_CHANGED
        detail = ""
    else:
        status = stored_status

    return {
        "status": status,
        "ok": status == STATUS_OK,
        "checked_at": (
            "" if status in (STATUS_NEVER_CHECKED, STATUS_NO_KEY) else checked_at
        ),
        "detail": detail,
        "blocker": blocker_sentence(status, detail, spec),
        "key_set": bool(api_key),
        "stale": stale,
        # The gate's own answer, precomputed so a UI never has to re-derive
        # the rule and drift from the server that enforces it.
        "can_enable": can_enable(status),
    }


def can_enable(status: str) -> bool:
    """May a provider in this verification state be *newly* enabled?

    Never-checked is refused (that is the whole feature). A conclusive
    failure is refused. Everything else — inconclusive, unreachable, a
    timeout — is allowed, because none of those is evidence the credential is
    bad and refusing on them would mean a flaky network can stop a user
    configuring failover.
    """
    return status not in CONCLUSIVE_FAILURES and status not in (
        STATUS_NEVER_CHECKED,
        STATUS_KEY_CHANGED,
    )


#: `gate_decision` verdicts.
GATE_ALLOW = "allow"
GATE_DEMOTE = "demote"
GATE_UNVERIFIED = "unverified"


def gate_decision(
    provider_cfg: CloudProviderConfig | None,
    spec: CloudProviderSpec,
    env: Mapping[str, str] | None = None,
) -> tuple[str, str]:
    """The write-path verdict for one enabled provider: `(verdict, reason)`.

    Separate from `verification_state` on purpose, and the difference is
    *whose environment is being read*. `verification_state` renders what the
    agent sees, and the agent's environment is the router's environment, so
    an unset variable there really does mean "no key". This runs on the write
    path, which is sometimes a CLI process — `netllm config import` is the
    macOS Save button, and that app keeps its keys in the login Keychain and
    injects them into the *agent*, not into the subprocess doing the save. A
    guard that read "I cannot see this key" as "there is no key" would demote
    every provider a macOS user has ever configured.

    So the two rules are drawn to be provable from either process:

    * `demote` — the entry names no credential source at all: no inline key,
      no `api_key_env`, nothing in the environment. Nothing anywhere could
      supply one, in any process, so the provider has never served a request
      and switching it off takes nothing away.
    * `unverified` — a credential exists but no current, passing check
      backs it. Fingerprints are only compared when this process can actually
      see the key; when it cannot, the stored record is taken at its word,
      which is the best available evidence rather than a guess.
    """
    cfg = provider_cfg
    key = resolve_api_key(cfg, spec, env)
    declares_source = bool(cfg and (cfg.api_key or cfg.api_key_env)) or bool(key)
    if not declares_source:
        return (
            GATE_DEMOTE,
            blocker_sentence(STATUS_NO_KEY, "", spec),
        )
    stored_status = (cfg.verified_status if cfg else "") or ""
    if not stored_status:
        return (GATE_UNVERIFIED, blocker_sentence(STATUS_NEVER_CHECKED, "", spec))
    if key and (cfg is None or cfg.verified_key_fingerprint != key_fingerprint(key)):
        return (GATE_UNVERIFIED, blocker_sentence(STATUS_KEY_CHANGED, "", spec))
    if stored_status in CONCLUSIVE_FAILURES:
        detail = (cfg.verified_detail if cfg else "") or ""
        return (GATE_UNVERIFIED, blocker_sentence(stored_status, detail, spec))
    return (GATE_ALLOW, "")


def record_verification(
    provider_cfg: CloudProviderConfig, result: Mapping[str, Any]
) -> None:
    """Persist a probe outcome onto the provider entry.

    Only the four read-only fields are touched, and the fingerprint comes
    from the probe result rather than from the stored key — that is what lets
    a check run against an unsaved key still count once that key is saved.
    """
    provider_cfg.verified_status = str(result.get("status", ""))
    provider_cfg.verified_at = str(result.get("checked_at", ""))
    provider_cfg.verified_detail = str(result.get("detail", "") or "")
    provider_cfg.verified_key_fingerprint = str(result.get("key_fingerprint", ""))


def _result(
    status: str,
    *,
    api_key: str,
    detail: str = "",
    spec: CloudProviderSpec,
    http_status: int | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "ok": status == STATUS_OK,
        "checked_at": _now(),
        "detail": detail,
        "blocker": blocker_sentence(status, detail, spec),
        "http_status": http_status,
        # Never the key: the fingerprint is what the gate compares, and it is
        # one-way. Nothing in this dict can be replayed against the provider.
        "key_fingerprint": key_fingerprint(api_key),
    }


def _catalog_request(base_url: str, api_format: str, api_key: str) -> tuple[str, dict]:
    if api_format == "anthropic":
        return (
            base_url.rstrip("/") + "/v1/models",
            {"x-api-key": api_key, "anthropic-version": "2023-06-01"},
        )
    return (
        base_url.rstrip("/") + "/models",
        {"Authorization": f"Bearer {api_key}"},
    )


def _minimal_call(
    base_url: str, api_format: str, api_key: str, model: str
) -> tuple[str, dict, dict]:
    """A one-token request — the only honest check for a provider with no
    catalogue endpoint.

    Z.ai-class providers publish no ``GET /models``, so "the key is set"
    would otherwise be the strongest thing anyone could say about them, which
    is precisely the claim this feature exists to stop making. One token is
    the smallest real proof that the credential authenticates, and it only
    happens for providers the registry marks ``models_endpoint=False`` — a
    provider with a catalogue is never billed for a verification.
    """
    if api_format == "anthropic":
        return (
            base_url.rstrip("/") + "/v1/messages",
            {"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            {
                "model": model,
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "ping"}],
            },
        )
    return (
        base_url.rstrip("/") + "/chat/completions",
        {"Authorization": f"Bearer {api_key}"},
        {
            "model": model,
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "ping"}],
        },
    )


def _status_for_http(code: int) -> str:
    if code == 200:
        return STATUS_OK
    if code in (401, 403):
        return STATUS_UNAUTHORIZED
    if code == 429:
        # Rate-limited means the credential was recognised well enough to be
        # counted against — it is not proof of a bad key.
        return STATUS_INCONCLUSIVE
    return STATUS_ERROR


async def probe_cloud_provider(
    provider_cfg: CloudProviderConfig | None,
    spec: CloudProviderSpec,
    *,
    api_key: str | None = None,
    env: Mapping[str, str] | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Check one provider's credential against the provider itself.

    ``api_key`` overrides the configured credential and is never persisted,
    never logged and never echoed back — the caller passes a key the user has
    typed but not yet saved, so nobody has to save a broken key to discover
    that it is broken.
    """
    cfg = provider_cfg
    key = (api_key or "").strip() or resolve_api_key(cfg, spec, env)
    if not key:
        return _result(STATUS_NO_KEY, api_key="", spec=spec)

    base_url, api_format = resolve_endpoint(cfg, spec)
    if not base_url:
        return _result(
            STATUS_NO_ENDPOINT,
            api_key=key,
            spec=spec,
            detail=f"{spec.display_name} serves no {api_format} endpoint here.",
        )

    owns_client = client is None
    http = client or httpx.AsyncClient()
    try:
        if spec.models_endpoint:
            url, headers = _catalog_request(base_url, api_format, key)
            try:
                resp = await http.get(url, headers=headers, timeout=timeout_s)
            except httpx.ConnectError:
                return _result(
                    STATUS_UNREACHABLE, api_key=key, spec=spec, detail=base_url
                )
            except httpx.TimeoutException:
                return _result(
                    STATUS_TIMEOUT,
                    api_key=key,
                    spec=spec,
                    detail=f"No response within {timeout_s:g}s.",
                )
            except Exception as exc:  # noqa: BLE001 — probe surface, never raises
                return _result(
                    STATUS_ERROR, api_key=key, spec=spec, detail=str(exc)[:200]
                )
            status = _status_for_http(resp.status_code)
            if status == STATUS_OK:
                count = _model_count(resp)
                return _result(
                    STATUS_OK,
                    api_key=key,
                    spec=spec,
                    http_status=200,
                    detail=f"Key accepted — {count} model(s) listed.",
                )
            return _result(
                status,
                api_key=key,
                spec=spec,
                http_status=resp.status_code,
                detail=_http_detail(resp),
            )

        model = _probe_model(cfg, spec)
        if not model:
            return _result(
                STATUS_INCONCLUSIVE,
                api_key=key,
                spec=spec,
                detail=(
                    f"{spec.display_name} publishes no model list and this "
                    "build knows no model to test with."
                ),
            )
        url, headers, body = _minimal_call(base_url, api_format, key, model)
        try:
            resp = await http.post(url, headers=headers, json=body, timeout=timeout_s)
        except httpx.ConnectError:
            return _result(STATUS_UNREACHABLE, api_key=key, spec=spec, detail=base_url)
        except httpx.TimeoutException:
            return _result(
                STATUS_TIMEOUT,
                api_key=key,
                spec=spec,
                detail=f"No response within {timeout_s:g}s.",
            )
        except Exception as exc:  # noqa: BLE001 — probe surface, never raises
            return _result(STATUS_ERROR, api_key=key, spec=spec, detail=str(exc)[:200])
        status = _status_for_http(resp.status_code)
        if status == STATUS_OK:
            return _result(
                STATUS_OK,
                api_key=key,
                spec=spec,
                http_status=200,
                detail=(
                    f"Key accepted — {spec.display_name} has no model-list API, "
                    "so this was a one-token test request."
                ),
            )
        if status == STATUS_ERROR:
            # A 400/404 here is usually "that model id is gone", which says
            # nothing about the credential. Refusing to enable on it would
            # punish the user for a stale entry in *our* registry.
            return _result(
                STATUS_INCONCLUSIVE,
                api_key=key,
                spec=spec,
                http_status=resp.status_code,
                detail=(
                    f"The one-token test with {model!r} returned "
                    f"HTTP {resp.status_code}: {_http_detail(resp)}"
                ),
            )
        return _result(
            status,
            api_key=key,
            spec=spec,
            http_status=resp.status_code,
            detail=_http_detail(resp),
        )
    finally:
        if owns_client:
            await http.aclose()


def _probe_model(cfg: CloudProviderConfig | None, spec: CloudProviderSpec) -> str:
    if cfg is not None and cfg.models:
        return cfg.models[0]
    return spec.static_models[0] if spec.static_models else ""


def _model_count(resp: httpx.Response) -> int:
    try:
        body = resp.json()
    except ValueError:
        return 0
    data = body.get("data") if isinstance(body, dict) else None
    return len(data) if isinstance(data, list) else 0


def _http_detail(resp: httpx.Response) -> str:
    if resp.status_code in (401, 403):
        return f"HTTP {resp.status_code}."
    text = (resp.text or "").strip().replace("\n", " ")
    if not text:
        return f"HTTP {resp.status_code}."
    return f"HTTP {resp.status_code}: {text[:160]}"


__all__ = [
    "CONCLUSIVE_FAILURES",
    "GATE_ALLOW",
    "GATE_DEMOTE",
    "GATE_UNVERIFIED",
    "STATUS_ERROR",
    "STATUS_INCONCLUSIVE",
    "STATUS_KEY_CHANGED",
    "STATUS_NEVER_CHECKED",
    "STATUS_NO_ENDPOINT",
    "STATUS_NO_KEY",
    "STATUS_OK",
    "STATUS_TIMEOUT",
    "STATUS_UNAUTHORIZED",
    "STATUS_UNREACHABLE",
    "VERIFICATION_FIELDS",
    "blocker_sentence",
    "can_enable",
    "gate_decision",
    "key_fingerprint",
    "probe_cloud_provider",
    "record_verification",
    "resolve_api_key",
    "resolve_endpoint",
    "verification_state",
]
