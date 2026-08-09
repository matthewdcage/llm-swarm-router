"""The local inference providers netllm can discover, stated once.

Lives in **core**, not discovery, because `models.py`, the CLI's `ui.py` and
`platform.py` all need these facts and discovery already depends on core --
putting it in discovery would invert that edge.

Before this module the same provider id was keyed in eleven parallel maps
across five files (`docs/extending/extension-cost-map.md`, Axis B). Nothing
referenced the roster in a test, so adding a provider meant finding all
eleven by archaeology and silently half-working if you missed one -- the
defect shape both audits kept producing (D17, F-35, F-45).

The rule (`docs/extending/PROGRAM.md` §1): a fact is stated once here, and
everything downstream derives it, generates it with `--check`, or is
projection-tested. `tests/conformance/kit_local.py` parameterizes over
`LOCAL_PROVIDERS`, so a new entry acquires its whole suite with no test edit.

`ProviderId` deliberately stays a hand-written `Literal` in `models.py`
(basedpyright is configured; a derived Literal blinds it) with a `get_args`
equality assertion in the kit.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class LocalProviderSpec:
    """Everything netllm knows about one local inference server."""

    id: str
    display_name: str
    """Long form, used by discovery result rows (`local.py`'s `name`)."""

    short_label: str
    """Compact form for CLI tables. Differs from `display_name` for oMLX,
    which carries an "(Apple Silicon)" qualifier that is noise in a table."""

    default_ports: tuple[int, ...]
    """Scanned on both 127.0.0.1 and localhost, in order, after config
    overrides and env vars."""

    platforms: tuple[str, ...]
    """`sys.platform` values where this provider is enabled by default.
    oMLX is Apple-Silicon only, which is why `default_discovery_providers()`
    had a darwin branch."""

    port_env: str = ""
    """Env var holding a comma/semicolon separated port list. Empty means the
    provider has none."""

    api_key_env: str = ""
    """Env var holding the API key. Every current entry is
    `f"{id.upper()}_API_KEY"`, but this is declared rather than derived: the
    lookup must *miss* for non-registry providers like `custom` and `peer:*`,
    which must produce no hint rather than a `CUSTOM_API_KEY` nothing reads."""

    default_api_key: str = ""
    """Key used when neither config nor env supplies one. Only oMLX has one
    (`omlx-local`), which is also the sentinel its auth hint reports."""

    host_env: str = ""
    """Env var holding a full host, `host:port`, `:port`, or URL. This is the
    Ollama case; declaring it removes `local.py`'s `if provider_id ==
    "ollama"` special case in favour of a generic candidate builder."""

    default_host_port: int = 0
    """Port assumed when `host_env` names a bare host with no port."""

    offline_hint_port: int = 0
    """Port quoted in the CLI's "nothing found" hint. Defaults to the first
    entry of `default_ports` when unset."""

    offline_hint: str = ""
    """How an operator starts this server, in imperative prose, for the CLI's
    "nothing found" output. Genuinely per-provider content rather than a copy
    of the roster -- `ollama serve` and `vllm serve --host 0.0.0.0` are not
    derivable from an id -- which is exactly why it belongs on the spec
    instead of an `elif` chain in `ui.py`."""

    aliases: tuple[str, ...] = field(default_factory=tuple)
    """Alternative ids accepted from config. None today; present so adding
    one is a registry edit rather than a new map."""

    def env_port_names(self) -> tuple[str, ...]:
        return (self.port_env,) if self.port_env else ()

    def hint_port(self) -> int:
        if self.offline_hint_port:
            return self.offline_hint_port
        return self.default_ports[0] if self.default_ports else 0


LOCAL_PROVIDERS: dict[str, LocalProviderSpec] = {
    "omlx": LocalProviderSpec(
        id="omlx",
        display_name="oMLX (Apple Silicon)",
        short_label="oMLX",
        default_ports=(8080, 8088, 8081),
        platforms=("darwin",),
        port_env="OMLX_PORT",
        api_key_env="OMLX_API_KEY",
        default_api_key="omlx-local",
        offline_hint="start the server",
    ),
    "ollama": LocalProviderSpec(
        id="ollama",
        display_name="Ollama",
        short_label="Ollama",
        default_ports=(11434,),
        platforms=("darwin", "linux", "win32"),
        port_env="OLLAMA_PORT",
        api_key_env="OLLAMA_API_KEY",
        host_env="OLLAMA_HOST",
        default_host_port=11434,
        offline_hint="run [cyan]ollama serve[/]",
    ),
    "lmstudio": LocalProviderSpec(
        id="lmstudio",
        display_name="LM Studio",
        short_label="LM Studio",
        default_ports=(1234, 41334),
        platforms=("darwin", "linux", "win32"),
        port_env="LMSTUDIO_PORT",
        api_key_env="LMSTUDIO_API_KEY",
        offline_hint="enable the local API server",
    ),
    "vllm": LocalProviderSpec(
        id="vllm",
        display_name="vLLM",
        short_label="vLLM",
        default_ports=(8000, 8001),
        platforms=("darwin", "linux", "win32"),
        port_env="VLLM_PORT",
        api_key_env="VLLM_API_KEY",
        offline_hint="run [cyan]vllm serve --host 0.0.0.0 --port 8000[/]",
    ),
}

# `custom` is not a discoverable provider -- it has no ports, no probe and no
# key -- but it IS a routing provider id and the CLI labels it alongside the
# real ones. `ui.py` therefore parameterizes over LOCAL_PROVIDERS | this,
# while port/probe logic parameterizes over LOCAL_PROVIDERS alone. The
# asymmetry is real; naming it here stops it being rediscovered as a bug.
NON_DISCOVERABLE_LABELS: dict[str, str] = {"custom": "custom endpoints"}


def get_local_provider_spec(provider_id: str) -> LocalProviderSpec | None:
    """Spec for an id, or None. None is meaningful: `custom`, `peer:*` and
    cloud ids all legitimately miss, and callers must stay silent for them
    rather than synthesizing a default."""
    spec = LOCAL_PROVIDERS.get(provider_id)
    if spec is not None:
        return spec
    for candidate in LOCAL_PROVIDERS.values():
        if provider_id in candidate.aliases:
            return candidate
    return None


def local_provider_ids() -> tuple[str, ...]:
    return tuple(LOCAL_PROVIDERS)


def api_key_env_for(provider_id: str) -> str:
    """Env var holding this provider's key, or "" when it has none.

    The empty return is load-bearing -- `netllm doctor` uses it to decide
    whether to name an env var in a 401 fix hint, and a `custom` backend must
    get the generic message.
    """
    spec = get_local_provider_spec(provider_id)
    return spec.api_key_env if spec else ""


def default_api_key_for(provider_id: str) -> str:
    spec = get_local_provider_spec(provider_id)
    return spec.default_api_key if spec else ""


def _is_platform_exclusive(spec: LocalProviderSpec) -> bool:
    """True when a provider runs on exactly one platform (oMLX today).

    Such a provider must never be offered on an unknown platform; a
    cross-platform one is a better guess than an empty list.
    """
    return len(spec.platforms) == 1


def providers_for_platform(sys_platform: str) -> list[str]:
    """Providers enabled by default on `sys_platform`.

    The fallback matters. `platforms` is an allowlist, but the behaviour it
    replaced was "everything except oMLX unless darwin" -- an else-branch that
    covered *any* platform, including ones nobody enumerated (freebsd, aix,
    cygwin). A bare allowlist silently returns nothing there, which turns an
    unusual platform from "works, minus oMLX" into "discovers nothing at all".
    Anything not enumerated therefore gets the cross-platform providers.
    """
    enabled = [
        spec.id for spec in LOCAL_PROVIDERS.values() if sys_platform in spec.platforms
    ]
    if enabled:
        return enabled
    return [
        spec.id for spec in LOCAL_PROVIDERS.values() if not _is_platform_exclusive(spec)
    ]


def _is_platform_exclusive(spec: LocalProviderSpec) -> bool:
    """True when a provider runs on exactly one platform (oMLX today).

    Such a provider must never be offered on an unknown platform; a
    cross-platform one is a better guess than an empty list.
    """
    return len(spec.platforms) == 1
