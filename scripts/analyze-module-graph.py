#!/usr/bin/env python3
"""AST dependency-graph analyzer for the F-26 module split.

Extracts, for a single Python module:

* **Nodes** - every module-level ``def``/``async def``/``class``, plus every method
  defined directly inside a class body. For ``service.py`` the ``AgentService``
  methods are the interesting nodes, so methods are first-class node identities
  (``AgentService.proxy_chat_completion``), not children of a class node.
* **Call edges** - ``self.foo(...)`` resolved to ``<Class>.foo`` when that method
  exists on the same class; bare ``foo(...)`` resolved to a module-level ``def``.
  Everything else is unresolvable/external and is only counted, per node, as
  external fan-out.
* **State edges** - every ``self.<attr>`` touched by a node, split into reads and
  writes (a write is an ``Assign``/``AugAssign``/``AnnAssign``/``for``/``with``
  target, or a ``del``).
* **Global edges** - references to module-level assigned names (constants,
  loggers, Typer apps).
* **Import usage** - which imported symbol each node uses, so a split can compute
  each new module's import list mechanically.

Clusters (the *proposed* post-split modules) are declared in ``CLUSTER_MAPS``
below, transcribed from ``docs/architecture/refactor/module-inventory.md`` and
``docs/architecture/refactor/plan-f24-f26.md`` section 1. The analyzer then reports
per-cluster cohesion, the exact cross-cluster edges, shared-state groups, and
cluster-level cycles.

Standard library only. Deterministic: every collection is sorted before output.

Usage::

    python3 scripts/analyze-module-graph.py --layout service-inventory \\
        --file packages/netllm-agent/src/netllm_agent/service.py

    git show HEAD:packages/netllm-cli/src/netllm_cli/main.py > /tmp/main_HEAD.py
    python3 scripts/analyze-module-graph.py --layout cli-inventory \\
        --file /tmp/main_HEAD.py

Add ``--format json`` for the machine-readable report.

Known limitations (do not over-trust the output): dynamic dispatch, ``getattr``,
string-keyed dispatch tables, decorators that wrap or re-register callables,
mixin/MRO composition across future modules, calls made through a local alias
(``fn = self.foo; fn()``), and attributes reached through anything other than a
literal ``self`` receiver are all invisible to a pure-AST walk.
"""

from __future__ import annotations

import argparse
import ast
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------- #
# Cluster maps (proposed post-split module for each node)
# --------------------------------------------------------------------------- #

# Layout from module-inventory.md 1.4 ("Recommended target layout").
SERVICE_INVENTORY: dict[str, str] = {
    # Cluster A -> service/core.py
    "AgentService.__init__": "core.py",
    "AgentService.apply_config": "core.py",
    "SourceCapacityExceeded.__init__": "core.py",
    # Cluster B -> service/status.py (telemetry sinks)
    "_token_count": "status.py",
    "AgentService._usage_from_response": "status.py",
    "AgentService._usage_from_sse_chunk": "status.py",
    "AgentService._record_success_telemetry": "status.py",
    "AgentService._record_stream_success": "status.py",
    "AgentService._update_health_metrics": "status.py",
    # Cluster C + F -> service/backends.py
    "AgentService.refresh_local_backends": "backends.py",
    "AgentService._scan_local_backends": "backends.py",
    "AgentService._peer_forward_headers": "backends.py",
    "AgentService._upstream_api_key": "backends.py",
    "AgentService._openai_upstream": "backends.py",
    # Cluster D -> service/status.py, minus heartbeat/gateway
    "AgentService.status_payload": "status.py",
    "AgentService.peer_config_warnings": "status.py",
    "AgentService.status_payload_enriched": "status.py",
    "AgentService.list_models_aggregated": "status.py",
    "AgentService._maybe_follow_gateway": "swarm_tasks.py",
    "AgentService.handle_heartbeat": "swarm_tasks.py",
    # Cluster E -> service/policy.py, except _mark_backend_failure -> selection.py
    "AgentService._mark_backend_failure": "selection.py",
    "AgentService._normalize_headers": "policy.py",
    "AgentService._incoming_hops": "policy.py",
    "AgentService._wants_local_only": "policy.py",
    "AgentService._model_for_backend": "policy.py",
    "AgentService._model_not_found_error": "policy.py",
    "AgentService._reject_non_chat_model": "policy.py",
    "AgentService._reject_non_chat_messages_model": "policy.py",
    "AgentService._restore_sse_line_model": "proxy.py",
    "AgentService._restore_stream_model": "proxy.py",
    # Cluster G -> service/policy.py
    "AgentService._resolved_routing": "policy.py",
    "AgentService._attribute_source": "policy.py",
    "AgentService._source_config": "policy.py",
    "AgentService._apply_source_model_rewrite": "policy.py",
    "AgentService._classify_and_record_scenario": "policy.py",
    "AgentService._apply_scenario_model": "policy.py",
    "AgentService._source_admit": "policy.py",
    "AgentService._source_release": "policy.py",
    # Cluster H -> service/selection.py
    "AgentService._select_backend_for_request": "selection.py",
    "AgentService._mark_shard_success": "selection.py",
    "AgentService._offload_if_probing": "selection.py",
    # Cluster I -> service/proxy.py
    "AgentService.proxy_chat_completion": "proxy.py",
    "AgentService.proxy_chat_completion_stream": "proxy.py",
    "AgentService.proxy_responses": "proxy.py",
    "AgentService.proxy_responses_stream": "proxy.py",
    "AgentService.proxy_embeddings": "proxy.py",
    "AgentService._stream_with_metrics": "proxy.py",
    # Cluster J -> service/cloud.py
    "AgentService._anthropic_api_key": "cloud.py",
    "AgentService._openai_api_key": "cloud.py",
    "AgentService._anthropic_default_headers": "cloud.py",
    "AgentService._legacy_openai_cloud_backend": "cloud.py",
    "AgentService._legacy_anthropic_cloud_backend": "cloud.py",
    "AgentService._materialize_cloud_provider_backends": "cloud.py",
    "AgentService.cloud_provider_models_probe": "cloud.py",
    # Cluster K -> service/messages.py
    "AgentService._anthropic_fallback_backends": "messages.py",
    "AgentService._messages_attempt": "messages.py",
    "AgentService.proxy_messages": "messages.py",
    "AgentService.proxy_messages_stream": "messages.py",
    "AgentService._messages_on_backend": "messages.py",
    "AgentService._messages_stream_on_backend": "messages.py",
    # Cluster L -> service/swarm_tasks.py
    "AgentService._try_start_mdns": "swarm_tasks.py",
    "AgentService.start_background": "swarm_tasks.py",
    "AgentService._rediscovery_loop": "swarm_tasks.py",
    "AgentService._spawn_background": "swarm_tasks.py",
    "AgentService._should_auto_subnet_fallback": "swarm_tasks.py",
    "AgentService._mdns_fallback_subnet_scan": "swarm_tasks.py",
    "AgentService._discover_subnet_peers": "swarm_tasks.py",
    "AgentService.stop_background": "swarm_tasks.py",
    # Class nodes themselves
    "SourceCapacityExceeded": "core.py",
    "AgentService": "__init__.py",
}

# Layout from plan-f24-f26.md section 1 ("Target end state"). Differs from the
# inventory layout by (a) splitting proxy.py into engine.py + surfaces/*, and
# (b) hoisting the success/failure accounting into accounting.py.
SERVICE_PLAN: dict[str, str] = dict(SERVICE_INVENTORY)
SERVICE_PLAN.update(
    {
        "_token_count": "accounting.py",
        "AgentService._usage_from_response": "accounting.py",
        "AgentService._usage_from_sse_chunk": "accounting.py",
        "AgentService._record_success_telemetry": "accounting.py",
        "AgentService._record_stream_success": "accounting.py",
        "AgentService._mark_backend_failure": "accounting.py",
        "AgentService._update_health_metrics": "status.py",
        "AgentService._stream_with_metrics": "engine.py",
        "AgentService._restore_sse_line_model": "surfaces/base.py",
        "AgentService._restore_stream_model": "surfaces/base.py",
        "AgentService.proxy_chat_completion": "surfaces/chat.py",
        "AgentService.proxy_chat_completion_stream": "surfaces/chat.py",
        "AgentService.proxy_responses": "surfaces/responses.py",
        "AgentService.proxy_responses_stream": "surfaces/responses.py",
        "AgentService.proxy_embeddings": "surfaces/embeddings.py",
        "AgentService._anthropic_fallback_backends": "surfaces/messages.py",
        "AgentService._messages_attempt": "surfaces/messages.py",
        "AgentService.proxy_messages": "surfaces/messages.py",
        "AgentService.proxy_messages_stream": "surfaces/messages.py",
        "AgentService._messages_on_backend": "surfaces/messages.py",
        "AgentService._messages_stream_on_backend": "surfaces/messages.py",
    }
)

# Layout from module-inventory.md 2.4 ("Recommended target layout").
CLI_INVENTORY: dict[str, str] = {
    "_version_callback": "main.py",
    "main": "main.py",
    "_config_path_option": "commands/_common.py",
    "_require_config": "commands/_common.py",
    "_normalize_agent_url": "commands/_common.py",
    "_resolve_init_swarm_mode": "commands/init_install.py",
    "_listen_port_of": "commands/init_install.py",
    "_apply_open_swarm_mode": "commands/init_install.py",
    "_apply_secured_swarm_mode": "commands/init_install.py",
    "_join_command_for": "commands/init_install.py",
    "_print_swarm_summary": "commands/init_install.py",
    "_swarm_next_steps": "commands/init_install.py",
    "_run_init_post_save": "commands/init_install.py",
    "init": "commands/init_install.py",
    "install": "commands/init_install.py",
    "discover": "commands/join_swarm.py",
    "_fetch_join_status": "commands/join_swarm.py",
    "_validate_join_token": "commands/join_swarm.py",
    "join": "commands/join_swarm.py",
    "_apply_swarm_join_listen": "commands/join_swarm.py",
    "swarm_token": "commands/join_swarm.py",
    "models": "commands/observe.py",
    "peers": "commands/observe.py",
    "env_shell": "commands/observe.py",
    "drain": "commands/observe.py",
    "status": "commands/observe.py",
    "serve": "commands/serve_lifecycle.py",
    "start": "commands/serve_lifecycle.py",
    "stop": "commands/serve_lifecycle.py",
    "restart": "commands/serve_lifecycle.py",
    "config_edit": "commands/serve_lifecycle.py",
    "_test_anthropic_agent": "commands/diagnose.py",
    "test": "commands/diagnose.py",
    "gateway_enable": "commands/diagnose.py",
    "doctor": "commands/diagnose.py",
    "config_export": "commands/config_io.py",
    "config_schema_cmd": "commands/config_io.py",
    "config_import_cmd": "commands/config_io.py",
    "_cloud_provider_id_or_exit": "commands/cloud.py",
    "cloud_list": "commands/cloud.py",
    "cloud_enable": "commands/cloud.py",
    "cloud_disable": "commands/cloud.py",
    "cloud_set_key": "commands/cloud.py",
    "_fallback_order_line": "commands/cloud.py",
    "cloud_fallback": "commands/cloud.py",
    "cloud_test": "commands/cloud.py",
    "cloud_connect": "commands/cloud.py",
    "sources_list": "commands/sources.py",
    "sources_toggle": "commands/sources.py",
}

CLUSTER_MAPS: dict[str, dict[str, str]] = {
    "service-inventory": SERVICE_INVENTORY,
    "service-plan": SERVICE_PLAN,
    "cli-inventory": CLI_INVENTORY,
}


# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #


@dataclass
class Node:
    name: str
    kind: str  # "function" | "method" | "class"
    owner: str | None  # class name for methods
    lineno: int
    end_lineno: int
    calls: dict[str, list[int]] = field(default_factory=dict)  # target -> lines
    method_refs: dict[str, list[int]] = field(default_factory=dict)  # bound-method refs
    external_calls: dict[str, int] = field(default_factory=dict)  # label -> count
    reads: dict[str, list[int]] = field(default_factory=dict)  # self attr -> lines
    writes: dict[str, list[int]] = field(default_factory=dict)
    # "pool.acquire" -> lines: a method invoked ON an instance attribute. AST
    # cannot tell read from mutation here, so these are reported separately.
    attr_method_calls: dict[str, list[int]] = field(default_factory=dict)
    globals_used: dict[str, list[int]] = field(default_factory=dict)
    imports_used: dict[str, list[int]] = field(default_factory=dict)

    @property
    def external_fanout(self) -> int:
        return sum(self.external_calls.values())


def _add(bucket: dict[str, list[int]], key: str, line: int) -> None:
    bucket.setdefault(key, [])
    if line not in bucket[key]:
        bucket[key].append(line)


# --------------------------------------------------------------------------- #
# Collection
# --------------------------------------------------------------------------- #


def _is_self(expr: ast.expr) -> bool:
    return isinstance(expr, ast.Name) and expr.id == "self"


def _dotted(expr: ast.expr) -> str:
    """Best-effort dotted rendering of a call target, for external labelling."""
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        return f"{_dotted(expr.value)}.{expr.attr}"
    if isinstance(expr, ast.Call):
        return f"{_dotted(expr.func)}()"
    if isinstance(expr, ast.Subscript):
        return f"{_dotted(expr.value)}[]"
    if isinstance(expr, ast.Await):
        return _dotted(expr.value)
    return "<expr>"


class ModuleGraph:
    def __init__(self, source: str, path: str) -> None:
        self.path = path
        self.tree = ast.parse(source)
        self.nodes: dict[str, Node] = {}
        self.module_globals: set[str] = set()
        self.imported: set[str] = set()
        self.class_methods: dict[str, set[str]] = defaultdict(set)
        self.module_funcs: set[str] = set()
        self._collect_declarations()
        self._collect_bodies()

    # -- pass 1: what exists ------------------------------------------------ #

    def _collect_declarations(self) -> None:
        for stmt in self.tree.body:
            if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                for alias in stmt.names:
                    self.imported.add(alias.asname or alias.name.split(".")[0])
            elif isinstance(stmt, ast.Assign):
                for tgt in stmt.targets:
                    if isinstance(tgt, ast.Name):
                        self.module_globals.add(tgt.id)
            elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                self.module_globals.add(stmt.target.id)
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.module_funcs.add(stmt.name)
            elif isinstance(stmt, ast.ClassDef):
                for sub in stmt.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        self.class_methods[stmt.name].add(sub.name)

    # -- pass 2: what each node touches ------------------------------------- #

    def _collect_bodies(self) -> None:
        for stmt in self.tree.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                node = Node(
                    stmt.name,
                    "function",
                    None,
                    stmt.lineno,
                    stmt.end_lineno or stmt.lineno,
                )
                self.nodes[stmt.name] = node
                self._walk(node, stmt)
            elif isinstance(stmt, ast.ClassDef):
                cls = Node(
                    stmt.name,
                    "class",
                    None,
                    stmt.lineno,
                    stmt.end_lineno or stmt.lineno,
                )
                self.nodes[stmt.name] = cls
                for sub in stmt.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        name = f"{stmt.name}.{sub.name}"
                        node = Node(
                            name,
                            "method",
                            stmt.name,
                            sub.lineno,
                            sub.end_lineno or sub.lineno,
                        )
                        self.nodes[name] = node
                        self._walk(node, sub)

    def _walk(self, node: Node, fn: ast.AST) -> None:
        """Walk a def's full subtree (nested defs included, attributed to it)."""
        # Assignment targets first, so a write is not double-counted as a read.
        write_ids: set[int] = set()
        for sub in ast.walk(fn):
            targets: list[ast.expr] = []
            if isinstance(sub, ast.Assign):
                targets = list(sub.targets)
            elif isinstance(sub, (ast.AugAssign, ast.AnnAssign)):
                targets = [sub.target]
            elif isinstance(sub, (ast.For, ast.AsyncFor)):
                targets = [sub.target]
            elif isinstance(sub, ast.Delete):
                targets = list(sub.targets)
            elif isinstance(sub, (ast.With, ast.AsyncWith)):
                targets = [i.optional_vars for i in sub.items if i.optional_vars]
            for tgt in targets:
                for inner in ast.walk(tgt):
                    if isinstance(inner, ast.Attribute) and _is_self(inner.value):
                        _add(node.writes, inner.attr, inner.lineno)
                        write_ids.add(id(inner))
                        if isinstance(sub, ast.AugAssign):
                            _add(node.reads, inner.attr, inner.lineno)

        call_func_ids: set[int] = set()
        for sub in ast.walk(fn):
            if not isinstance(sub, ast.Call):
                continue
            func = sub.func
            call_func_ids.add(id(func))
            if isinstance(func, ast.Attribute) and _is_self(func.value):
                if node.owner and func.attr in self.class_methods.get(node.owner, ()):
                    _add(node.calls, f"{node.owner}.{func.attr}", sub.lineno)
                else:
                    # self.<x>() where <x> is not a method on this class: the
                    # attribute is state, the call is external.
                    _add(node.reads, func.attr, func.lineno)
                    label = f"self.{func.attr}()"
                    node.external_calls[label] = node.external_calls.get(label, 0) + 1
            elif (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Attribute)
                and _is_self(func.value.value)
            ):
                _add(
                    node.attr_method_calls,
                    f"{func.value.attr}.{func.attr}",
                    sub.lineno,
                )
                label = f"self.{func.value.attr}.{func.attr}()"
                node.external_calls[label] = node.external_calls.get(label, 0) + 1
            elif isinstance(func, ast.Name) and func.id in self.module_funcs:
                _add(node.calls, func.id, sub.lineno)
            else:
                label = _dotted(func) + "()"
                node.external_calls[label] = node.external_calls.get(label, 0) + 1

        for sub in ast.walk(fn):
            if isinstance(sub, ast.Attribute) and _is_self(sub.value):
                if id(sub) in write_ids or id(sub) in call_func_ids:
                    continue
                if node.owner and sub.attr in self.class_methods.get(node.owner, ()):
                    # Bound-method reference (e.g. passed as a callback): a real
                    # dependency on that method, not instance state.
                    _add(node.calls, f"{node.owner}.{sub.attr}", sub.lineno)
                    _add(node.method_refs, f"{node.owner}.{sub.attr}", sub.lineno)
                    continue
                _add(node.reads, sub.attr, sub.lineno)
            elif isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                if sub.id in self.module_globals:
                    _add(node.globals_used, sub.id, sub.lineno)
                elif sub.id in self.imported:
                    _add(node.imports_used, sub.id, sub.lineno)

        # Decorators sit outside the body but bind the node to module globals
        # (e.g. @app.command()) - attribute them to the node too.
        for dec in getattr(fn, "decorator_list", []):
            for sub in ast.walk(dec):
                if isinstance(sub, ast.Name):
                    if sub.id in self.module_globals:
                        _add(node.globals_used, sub.id, sub.lineno)
                    elif sub.id in self.imported:
                        _add(node.imports_used, sub.id, sub.lineno)


# --------------------------------------------------------------------------- #
# Cluster analysis
# --------------------------------------------------------------------------- #


def analyze(graph: ModuleGraph, clusters: dict[str, str]) -> dict:
    """Score the proposed cluster assignment against the extracted graph."""
    method_nodes = {
        name: n for name, n in graph.nodes.items() if n.kind in ("method", "function")
    }
    unmapped = sorted(n for n in method_nodes if n not in clusters)

    def cluster_of(name: str) -> str:
        return clusters.get(name, "UNMAPPED")

    # --- call edges ---
    call_edges: list[dict] = []
    for name in sorted(method_nodes):
        node = method_nodes[name]
        for target in sorted(node.calls):
            if target not in method_nodes:
                continue
            call_edges.append(
                {
                    "src": name,
                    "dst": target,
                    "kind": "call",
                    "lines": sorted(node.calls[target]),
                    "src_cluster": cluster_of(name),
                    "dst_cluster": cluster_of(target),
                }
            )

    # --- state edges: attribute -> touching clusters ---
    attr_readers: dict[str, set[str]] = defaultdict(set)
    attr_writers: dict[str, set[str]] = defaultdict(set)
    attr_reader_nodes: dict[str, set[str]] = defaultdict(set)
    attr_writer_nodes: dict[str, set[str]] = defaultdict(set)
    # Constructor writes are benign (one-time initialisation, not shared
    # mutation), so they are tracked separately from post-construction writers.
    attr_writers_excl_init: dict[str, set[str]] = defaultdict(set)
    attr_invoked: dict[str, set[str]] = defaultdict(set)  # attr -> methods called
    attr_invokers: dict[str, set[str]] = defaultdict(set)  # attr -> clusters
    # attr.meth -> clusters
    attr_method_clusters: dict[str, set[str]] = defaultdict(set)
    for name in sorted(method_nodes):
        node = method_nodes[name]
        for dotted in node.attr_method_calls:
            attr, meth = dotted.split(".", 1)
            attr_invoked[attr].add(meth)
            attr_invokers[attr].add(cluster_of(name))
            attr_method_clusters[dotted].add(cluster_of(name))
        for attr in node.reads:
            attr_readers[attr].add(cluster_of(name))
            attr_reader_nodes[attr].add(name)
        for attr in node.writes:
            attr_writers[attr].add(cluster_of(name))
            attr_writer_nodes[attr].add(name)
            if not name.endswith(".__init__"):
                attr_writers_excl_init[attr].add(cluster_of(name))

    all_attrs = sorted(set(attr_readers) | set(attr_writers) | set(attr_invokers))
    shared_state: list[dict] = []
    for attr in all_attrs:
        touching = attr_readers[attr] | attr_writers[attr] | attr_invokers[attr]
        if len(touching) <= 1:
            continue
        shared_state.append(
            {
                "attr": attr,
                "clusters": sorted(touching),
                "writers": sorted(attr_writers[attr]),
                "writers_excl_init": sorted(attr_writers_excl_init[attr]),
                "readers_only": sorted(attr_readers[attr] - attr_writers[attr]),
                "n_clusters": len(touching),
                "n_writer_clusters": len(attr_writers[attr]),
                "n_writer_clusters_excl_init": len(attr_writers_excl_init[attr]),
                "writer_nodes": sorted(attr_writer_nodes[attr]),
                "reader_node_count": len(attr_reader_nodes[attr]),
                "invoked_methods": sorted(attr_invoked[attr]),
                "invoked_by": {
                    m: sorted(attr_method_clusters[f"{attr}.{m}"])
                    for m in sorted(attr_invoked[attr])
                },
                "invoker_clusters": sorted(attr_invokers[attr]),
            }
        )
    shared_state.sort(
        key=lambda s: (
            -s["n_writer_clusters_excl_init"],
            -s["n_clusters"],
            s["attr"],
        )
    )

    # A state edge is: cluster X writes attr A, cluster Y (!= X) touches A.
    state_edges: list[dict] = []
    for attr in all_attrs:
        touch = attr_readers[attr] | attr_writers[attr] | attr_invokers[attr]
        for wc in sorted(attr_writers[attr] | attr_invokers[attr]):
            for tc in sorted(touch):
                if tc == wc:
                    continue
                state_edges.append(
                    {"src": wc, "dst": tc, "kind": "state", "attr": attr}
                )

    # --- global edges ---
    global_edges: list[dict] = []
    for name in sorted(method_nodes):
        for g in sorted(graph.nodes[name].globals_used):
            global_edges.append(
                {
                    "src": name,
                    "src_cluster": cluster_of(name),
                    "global": g,
                    "lines": sorted(graph.nodes[name].globals_used[g]),
                }
            )

    # --- per-cluster metrics ---
    cluster_names = sorted({cluster_of(n) for n in method_nodes})
    metrics: dict[str, dict] = {}
    for c in cluster_names:
        members = sorted(n for n in method_nodes if cluster_of(n) == c)
        internal_calls = sum(
            1 for e in call_edges if e["src_cluster"] == c and e["dst_cluster"] == c
        )
        out_calls = sum(
            1 for e in call_edges if e["src_cluster"] == c and e["dst_cluster"] != c
        )
        in_calls = sum(
            1 for e in call_edges if e["dst_cluster"] == c and e["src_cluster"] != c
        )
        cross_state = len(
            {
                (e["src"], e["dst"], e["attr"])
                for e in state_edges
                if c in (e["src"], e["dst"])
            }
        )
        denom = internal_calls + out_calls + in_calls
        metrics[c] = {
            "members": members,
            "n_nodes": len(members),
            "loc": sum(
                graph.nodes[m].end_lineno - graph.nodes[m].lineno + 1 for m in members
            ),
            "internal_calls": internal_calls,
            "out_calls": out_calls,
            "in_calls": in_calls,
            "cross_calls": out_calls + in_calls,
            "cohesion": round(internal_calls / denom, 3) if denom else None,
            "cross_state_edges": cross_state,
            "external_fanout": sum(graph.nodes[m].external_fanout for m in members),
            "imports": sorted(
                {i for m in members for i in graph.nodes[m].imports_used}
            ),
        }

    # --- cluster-level call graph with weights ---
    weights: dict[tuple[str, str], int] = defaultdict(int)
    for e in call_edges:
        if e["src_cluster"] != e["dst_cluster"]:
            weights[(e["src_cluster"], e["dst_cluster"])] += len(e["lines"])
    cluster_call_graph = [
        {"src": s, "dst": d, "weight": w} for (s, d), w in sorted(weights.items())
    ]

    # --- cycles (SCCs with > 1 member, plus explicit 2-cycles) ---
    adj: dict[str, set[str]] = defaultdict(set)
    for s, d in weights:
        adj[s].add(d)
    cycles = _sccs(cluster_names, adj)
    two_cycles = sorted(
        {tuple(sorted((s, d))) for (s, d) in weights if (d, s) in weights}
    )

    return {
        "path": graph.path,
        "unmapped_nodes": unmapped,
        "counts": {
            "nodes_total": len(graph.nodes),
            "functions_and_methods": len(method_nodes),
            "classes": sum(1 for n in graph.nodes.values() if n.kind == "class"),
            "call_edges": len(call_edges),
            "call_edge_sites": sum(len(e["lines"]) for e in call_edges),
            "cross_cluster_call_edges": sum(
                1 for e in call_edges if e["src_cluster"] != e["dst_cluster"]
            ),
            "self_attributes": len(all_attrs),
            "shared_state_groups": len(shared_state),
            "shared_state_multi_writer": sum(
                1 for s in shared_state if s["n_writer_clusters"] > 1
            ),
            "shared_state_multi_writer_excl_init": sum(
                1 for s in shared_state if s["n_writer_clusters_excl_init"] > 1
            ),
            "global_refs": len(global_edges),
            "external_fanout": sum(n.external_fanout for n in method_nodes.values()),
        },
        "clusters": metrics,
        "call_edges": call_edges,
        "cross_cluster_calls": [
            e for e in call_edges if e["src_cluster"] != e["dst_cluster"]
        ],
        "cluster_call_graph": cluster_call_graph,
        "shared_state": shared_state,
        "global_edges": global_edges,
        "cycles": {"sccs": cycles, "two_cycles": [list(t) for t in two_cycles]},
    }


def _sccs(nodes: list[str], adj: dict[str, set[str]]) -> list[list[str]]:
    """Tarjan SCCs; only components with more than one member are returned."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    out: list[list[str]] = []
    counter = [0]

    def strong(v: str) -> None:
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on_stack.add(v)
        for w in sorted(adj.get(v, ())):
            if w not in index:
                strong(w)
                low[v] = min(low[v], low[w])
            elif w in on_stack:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                comp.append(w)
                if w == v:
                    break
            if len(comp) > 1:
                out.append(sorted(comp))

    for v in sorted(nodes):
        if v not in index:
            strong(v)
    return sorted(out)


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def render_text(report: dict) -> str:
    lines: list[str] = []
    c = report["counts"]
    lines.append(f"# {report['path']}")
    lines.append("")
    lines.append("## Counts")
    for k in sorted(c):
        lines.append(f"  {k}: {c[k]}")
    if report["unmapped_nodes"]:
        lines.append("")
        lines.append("## UNMAPPED NODES (not covered by the proposed layout)")
        for n in report["unmapped_nodes"]:
            lines.append(f"  {n}")
    lines.append("")
    lines.append("## Cluster cohesion")
    header = (
        f"  {'cluster':<26}{'nodes':>6}{'loc':>7}{'int':>6}{'out':>6}"
        f"{'in':>5}{'cohesion':>10}{'state':>7}{'extfan':>8}"
    )
    lines.append(header)
    for name in sorted(report["clusters"]):
        m = report["clusters"][name]
        coh = "n/a" if m["cohesion"] is None else f"{m['cohesion']:.3f}"
        lines.append(
            f"  {name:<26}{m['n_nodes']:>6}{m['loc']:>7}{m['internal_calls']:>6}"
            f"{m['out_calls']:>6}{m['in_calls']:>5}{coh:>10}"
            f"{m['cross_state_edges']:>7}{m['external_fanout']:>8}"
        )
    lines.append("")
    lines.append("## Cluster call graph (cross-cluster only, weight = call sites)")
    for e in report["cluster_call_graph"]:
        lines.append(f"  {e['src']} -> {e['dst']}  w={e['weight']}")
    lines.append("")
    lines.append("## Cross-cluster call edges")
    for e in report["cross_cluster_calls"]:
        lines.append(
            f"  [{e['src_cluster']} -> {e['dst_cluster']}] {e['src']} -> {e['dst']} "
            f"@ {','.join(str(x) for x in e['lines'])}"
        )
    lines.append("")
    lines.append("## Shared state groups (attributes touched by >1 cluster)")
    for s in report["shared_state"]:
        lines.append(
            f"  self.{s['attr']}: clusters={s['clusters']}\n"
            f"      rebind-writers={s['writers_excl_init']} (+init) "
            f"read-only={s['readers_only']}\n"
            f"      method-invokers={s['invoker_clusters']} "
            f"methods={s['invoked_methods']}"
        )
    lines.append("")
    lines.append("## Cycles")
    lines.append(f"  two_cycles: {report['cycles']['two_cycles']}")
    lines.append(f"  sccs: {report['cycles']['sccs']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--file", required=True, help="Python module to analyze")
    ap.add_argument(
        "--layout",
        required=True,
        choices=sorted(CLUSTER_MAPS),
        help="Proposed cluster layout to validate against",
    )
    ap.add_argument("--format", default="text", choices=["text", "json"])
    args = ap.parse_args(argv)

    path = Path(args.file)
    graph = ModuleGraph(path.read_text(encoding="utf-8"), str(path))
    report = analyze(graph, CLUSTER_MAPS[args.layout])
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
