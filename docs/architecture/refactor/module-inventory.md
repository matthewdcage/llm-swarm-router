# Module Inventory for the F-26 Split

Analyzed at HEAD = `64f14a4` ("docs(architecture): follow-up audit 2026-07-31"). All line numbers below are against HEAD (`git show HEAD:<path>`), not the working tree, which is concurrently being edited by the F-30..F-48 remediation workflow.

Context anchors:
- **F-26** (docs/architecture/07-findings-register.md:712-721): `service.py` and `main.py` concentrate 11 of 29 findings; suggested split: `service/` package (`backends.py`, `policy.py`, `proxy.py`, `swarm_tasks.py`, `status.py`) and a `commands/` package for the CLI. Scoped out of the original remediation (07:815-817, "open (scoped out)").
- **F-24** (07:677-693): four near-identical ~70-line proxy loops; the prescribed fix — a `_RequestPlan` built once plus a generic `_run_with_failover(plan, invoke)` — is the seam this split must be designed around.
- **Live consequences of F-24 divergence** (docs/architecture/09-follow-up-audit-2026-07-31.md): **F-33** (09:143-157) — `proxy_messages_stream` (service.py:1904-1910 region) records no `mark_success`/`REQUESTS_TOTAL`/tokens, so streamed `/v1/messages` traffic (Claude Code's default) is invisible to the Serving UI and skews `least_load`/`latency_weighted`. **F-35** (09:176-184) — the c9bd30a payload adapter covers chat only; embeddings still 502s. Both are **in-flight fixes that land before this refactor starts**; the split must be rebased on them (see "Sequencing" below). 09:411-413 states plainly that fixes landing on one proxy loop but not its siblings "is now the dominant defect generator."
- Request-path semantics that must survive the move verbatim are specified in docs/architecture/03-request-lifecycle.md (four proxy surfaces, failover semantics, loop prevention, Anthropic fallback tier, concurrency accounting).

---

## Part 1 — `packages/netllm-agent/src/netllm_agent/service.py` (2,246 lines at HEAD)

### 1.1 Full top-level inventory

Module level:
| Item | Lines |
|---|---|
| Docstring + imports | 1–77 |
| `LEGACY_CLOUD_BACKEND_IDS` constant (F-04 rationale comment) | 79–84 |
| `class SourceCapacityExceeded(Exception)` | 87–99 |
| `class AgentService` | 102–2246 |

`AgentService` methods, grouped into clusters (every method of the class is listed exactly once):

**Cluster A — construction & config access**
| Method | Lines |
|---|---|
| `__init__` | 105–159 |
| `apply_config` | 251–272 |

**Cluster B — telemetry & metrics accounting**
| Method | Lines |
|---|---|
| `_usage_from_response` (static) | 161–172 |
| `_record_success_telemetry` | 174–197 |
| `_update_health_metrics` | 322–338 |

**Cluster C — backend pool refresh / local discovery scan**
| Method | Lines |
|---|---|
| `refresh_local_backends` | 199–249 |
| `_scan_local_backends` | 274–320 |

**Cluster D — status, heartbeat, peer/gateway coherence**
| Method | Lines |
|---|---|
| `status_payload` | 340–378 |
| `peer_config_warnings` | 380–405 |
| `status_payload_enriched` | 407–412 |
| `_maybe_follow_gateway` | 414–438 |
| `handle_heartbeat` | 440–458 |
| `list_models_aggregated` | 460–496 |

**Cluster E — request-path model/header utilities**
| Method | Lines |
|---|---|
| `_mark_backend_failure` | 498–509 |
| `_normalize_headers` (static) | 511–515 |
| `_incoming_hops` (static) | 517–520 |
| `_wants_local_only` (static) | 522–531 |
| `_model_for_backend` | 533–570 |
| `_model_not_found_error` | 572–588 |
| `_reject_non_chat_model` (static) | 590–609 |
| `_reject_non_chat_messages_model` (static) | 611–620 |
| `_restore_sse_line_model` (static) | 622–631 |
| `_restore_stream_model` (static) | 633–649 |

**Cluster F — upstream client construction & peer forwarding**
| Method | Lines |
|---|---|
| `_peer_forward_headers` (static) | 651–670 |
| `_upstream_api_key` | 672–680 |
| `_openai_upstream` (holds the `_upstream_cache`, F-04 request-scoped exception) | 682–715 |

**Cluster G — source admission & scenario/routing policy**
| Method | Lines |
|---|---|
| `_resolved_routing` | 717–737 |
| `_attribute_source` | 739–752 |
| `_source_config` | 754–758 |
| `_apply_source_model_rewrite` (static) | 760–764 |
| `_classify_and_record_scenario` | 766–786 |
| `_apply_scenario_model` (static) | 788–797 |
| `_source_admit` (F-08 check+reserve atomicity comment) | 799–818 |
| `_source_release` | 820–823 |

**Cluster H — backend selection (strategy + shard)**
| Method | Lines |
|---|---|
| `_select_backend_for_request` | 825–960 |
| `_mark_shard_success` | 962–964 |
| `_offload_if_probing` (F-03) | 966–972 |

**Cluster I — request engine, OpenAI surface (the F-24 duplication)**
| Method | Lines |
|---|---|
| `proxy_chat_completion` | 974–1089 |
| `proxy_chat_completion_stream` | 1091–1204 |
| `proxy_responses` (thin bridge over chat) | 1206–1222 |
| `proxy_responses_stream` | 1224–1235 |
| `proxy_embeddings` | 1237–1357 |
| `_stream_with_metrics` (physically stranded at the far end of the file) | 2019–2044 |

**Cluster J — cloud credentials & cloud backend injection**
| Method | Lines |
|---|---|
| `_anthropic_api_key` (static) | 1359–1364 |
| `_openai_api_key` (static) | 1366–1376 |
| `_anthropic_default_headers` (static) | 1378–1384 |
| `_legacy_openai_cloud_backend` (F-04 request-scoped rows) | 1386–1423 |
| `_legacy_anthropic_cloud_backend` | 1425–1446 |
| `_materialize_cloud_provider_backends` | 1448–1542 |
| `cloud_provider_models_probe` | 1544–1626 |

**Cluster K — Messages / Anthropic bridge glue**
| Method | Lines |
|---|---|
| `_anthropic_fallback_backends` | 1628–1646 |
| `_messages_attempt` | 1648–1702 |
| `proxy_messages` | 1704–1811 |
| `proxy_messages_stream` (F-33 lives here: return path ~1904–1910 records nothing) | 1813–1954 |
| `_messages_on_backend` | 1956–1983 |
| `_messages_stream_on_backend` | 1985–2017 |

**Cluster L — swarm background tasks (mDNS, rediscovery, subnet)**
| Method | Lines |
|---|---|
| `_try_start_mdns` | 2046–2112 |
| `start_background` | 2114–2146 |
| `_rediscovery_loop` | 2148–2185 |
| `_spawn_background` | 2187–2190 |
| `_should_auto_subnet_fallback` | 2192–2202 |
| `_mdns_fallback_subnet_scan` | 2204–2213 |
| `_discover_subnet_peers` | 2215–2239 |
| `stop_background` | 2241–2246 |

### 1.2 Coupling between clusters (call-site evidence)

Shared private helpers, with the exact caller lines:

| Helper (owner cluster) | Callers (cluster: lines) | Fan-out |
|---|---|---|
| `refresh_local_backends` (C) | D: 458, 461 · I: 1004, 1121, 1271 · K: 1735, 1844 · L: 2093, 2178, 2236 | **10 sites, 4 clusters** — the single most-shared method |
| `_materialize_cloud_provider_backends` (J) | D: 462 · I: 1010, 1127, 1277 · K: 1739, 1848 | 6 sites, 3 clusters |
| `_update_health_metrics` (B) | C: 248 · I: 1083, 1198, 1351 · K: 1702, 1943 | 6 sites, 3 clusters |
| `_normalize_headers` (E) | G: 726, 746 · I: 980, 1097, 1250 · K: 1710, 1819 | 7 sites, 3 clusters |
| `_select_backend_for_request` + `_offload_if_probing` (H) | I: 1018/1019, 1136/1137, 1287/1288 · K: 1755/1756, 1866/1867 | all 5 failover loops |
| Policy pipeline `_attribute_source → _source_config → _classify_and_record_scenario → _apply_source_model_rewrite → _apply_scenario_model → _resolved_routing → _source_admit … _source_release` (G) | Repeated verbatim at each of the 5 proxy entries: I: 982–1000/1089, 1099–1117/1204, 1252–1269/1357 · K: 1712–1733/1811, 1821–1842/1954 | the F-24 "plan" block, ×5 |
| `_openai_upstream` (F) | I: 1040, 1157, 1309 · K: 1981, 2012 | 5 sites, 2 clusters |
| `_model_for_backend` (E) | I: 1041, 1158, 1310 · K: 1980, 2011 | 5 sites, 2 clusters |
| `_mark_backend_failure` (E) | I: 1068, 1336, 2040 · K: 1688, 1914 | 5 sites, 2 clusters |
| `_record_success_telemetry` (B) | I: 1056, 1325 · K: 1679 | 3 sites — **and not called from either streaming path** (F-33) |
| `_legacy_openai_cloud_backend` / `_openai_api_key` (J) | I only: 1007/1008, 1124/1125, 1274/1275 | |
| `_legacy_anthropic_cloud_backend` / `_anthropic_api_key` (J) | K only: 1738/1725, 1847/1834 | |
| `_model_not_found_error` (E) | I only: 1087, 1202, 1355 | |
| `_restore_stream_model` (E) | I only: 1168 | |
| `_stream_with_metrics` (I) | I: 1164 (chat stream only; `proxy_messages_stream` has no equivalent — F-33) | |
| `_mark_shard_success` (H) | I: 1063, 2038 | |
| `_anthropic_fallback_backends` (K) | K only: 1785, 1883, 1892 | |
| `_spawn_background` (L) | L only: 2099, 2140, 2142, 2144 | |
| `_maybe_follow_gateway` (D) | D only: 444 | |

Shared mutable state (`self.*` attribute → writer/reader clusters):

| State | Written by | Read by |
|---|---|---|
| `self.pool` (65 refs) | A init:107, C merge/prune:237–247, I/K acquire/release/mark, J merge/prune:1537–1542 | B:335–336, D:341–350, E:551–579, H:841–949, I/K everywhere, K fallback:1641–1644 |
| `self.swarm` (19 refs) | A:119 | C:235, D:346–348, 390, 445, L: 2082–2242 |
| `self.telemetry` | A:159 | B:192, I:2037 |
| `self._upstream_cache` | F: 703–714 | F only — cleanly encapsulated |
| `self._source_counts` / `_scenario_counts` / `_source_in_flight` | G: 748, 784, 818, 821–822 | D status_payload: 352–356 |
| `self._batch_ledger` | H: 864–876, 964 | H only |
| `self._shardless_fallbacks` | H: 900, 904 | D: 357 |
| `self._request_count` | I: 1062, 1331 · K: 1685 | (write-only at HEAD — dead-ish counter) |
| `self._local_scan_cache/_at/_lock` | C: 216–233 · invalidated by A apply_config:272 | C only |
| `self._mdns_advertiser/_browser`, `_background_tasks` | L | L only |
| `self.draining`, `self.startup_warnings` | app.py admin route / L:2145 | D: 361 |

Reading of the coupling: **G, H, F, E are pure "request-plan" machinery consumed identically by all five loops; C and J are the only cross-cutting mutators of the pool outside the loops themselves; B is the accounting sink; L and D touch the request path only through `refresh_local_backends` and the counters they surface.** This is exactly the shape F-24's `_RequestPlan` + `_run_with_failover` needs.

### 1.3 External usages that constrain the split (every importer at HEAD)

Production code (import path must keep working unchanged):
- `packages/netllm-agent/src/netllm_agent/__init__.py:4` — `from netllm_agent.service import AgentService`
- `packages/netllm-agent/src/netllm_agent/admin.py:23` — `from netllm_agent.service import AgentService`; uses only `service.pool` (admin.py:78–83) and `service.peer_config_warnings` (admin.py:151)
- `packages/netllm-agent/src/netllm_agent/app.py:34` — `from netllm_agent.service import AgentService, SourceCapacityExceeded`; consumes exactly this public surface: `apply_config`, `cloud_provider_models_probe`, `draining`, `handle_heartbeat`, `list_models_aggregated`, `pool`, `proxy_chat_completion(_stream)`, `proxy_embeddings`, `proxy_messages(_stream)`, `proxy_responses(_stream)`, `refresh_local_backends`, `start_background`, `status_payload_enriched`, `stop_background`, `swarm`, `telemetry`

Tests (patch-target constraints — these fix *module namespaces*, not just names):
- **25 occurrences** of `patch("netllm_agent.service.scan_local_providers", …)` across tests/test_agent.py (90, 141, 520, 628, 736, 770, 794, 888…), test_anthropic_cloud_compat.py:24, test_cloud_routing.py:371–427, test_doctor_open_lan.py:75, test_embeddings.py:32–138, and more. This works today because `service.py:52` imports the name and `_scan_local_backends` (service.py:283) resolves it through the `netllm_agent.service` module globals. **If `_scan_local_backends` moves to `service/backends.py`, this patch target silently stops intercepting.**
- `patch("netllm_agent.service.AgentService.proxy_chat_completion")` (test_agent.py:150, test_codex_responses_bridge.py:349), `…proxy_messages` (test_agent.py:205, test_anthropic_cloud_compat.py:63), `…proxy_messages_stream` (test_agent.py:236) — these patch class attributes and survive any move as long as `netllm_agent.service.AgentService` keeps resolving to the class.
- Direct constructor imports in ~10 test files (test_admin_cloud.py:15, test_cloud_routing.py:10, test_drain_and_concurrency.py:10, test_agent.py multiple, test_model_aliases.py:63/79, test_doctor_open_lan.py:101/115).

### 1.4 Recommended target layout: `netllm_agent/service/` package

Strategy: **mechanical mixin split first, F-24 loop unification inside the new `proxy.py` as the second commit on the same branch.** Mixins (a single `AgentService` composed from per-module mixin classes) preserve every `self.*` coupling above with zero behavioural risk, keep all `AgentService.<method>` patch targets valid, and let each module stay under budget. Collaborator extraction (real objects owning their state) can follow later where state is already cleanly partitioned (`_upstream_cache`, `_batch_ledger`, mDNS handles).

| New module | Contents (clusters) | Est. size |
|---|---|---|
| `service/__init__.py` | Final `class AgentService(CoreInit, BackendsMixin, CloudMixin, PolicyMixin, ProxyMixin, MessagesMixin, StatusMixin, SwarmTasksMixin)` one-liner composition; re-export `AgentService`, `SourceCapacityExceeded`, `LEGACY_CLOUD_BACKEND_IDS`; **re-import `scan_local_providers` at module level and keep the call in `_scan_local_backends` resolving via this package's namespace** (or do the one-time mechanical repoint of the 25 test patch sites — see constraint above; pick one explicitly, don't get both half-done) | ~60 |
| `service/core.py` | Cluster A: `__init__` (105–159), `apply_config` (251–272), `SourceCapacityExceeded` (87–99), constants (79–84) | ~180 |
| `service/backends.py` | Cluster C (`refresh_local_backends`, `_scan_local_backends`) + Cluster F (`_peer_forward_headers`, `_upstream_api_key`, `_openai_upstream` and the `_upstream_cache` discipline) | ~230 |
| `service/cloud.py` | Cluster J entire (1359–1626): API-key extraction statics, legacy request-scoped rows (F-04 comments travel with the code), `_materialize_cloud_provider_backends`, `cloud_provider_models_probe` | ~330 |
| `service/policy.py` | Cluster G (717–823) + Cluster E's model utilities (`_model_for_backend`, `_model_not_found_error`, `_reject_non_chat_*`, `_normalize_headers`, `_incoming_hops`, `_wants_local_only`) — this module *is* the `_RequestPlan` builder after F-24 | ~380 |
| `service/selection.py` | Cluster H (825–972) + `_mark_backend_failure` (498–509) | ~220 |
| `service/proxy.py` | Cluster I + the F-24 extraction: `_RequestPlan` dataclass, `_run_with_failover(plan, invoke)` (owning acquire/mark_success/mark_failure/REQUESTS_TOTAL/latency/token accounting **once**, fixing the F-33 class of divergence structurally), thin `proxy_chat_completion(_stream)`, `proxy_embeddings`, `proxy_responses(_stream)`, `_stream_with_metrics`, `_restore_(sse_line|stream)_model` | ~500 post-F-24 (vs ~560 raw move) |
| `service/messages.py` | Cluster K (1628–2017): fallback-tier iterator, `_messages_attempt`, `proxy_messages(_stream)` as thin wrappers over the same `_run_with_failover` with the anthropic fallback tier as a second candidate source, `_messages_(stream_)on_backend` bridge glue | ~420 |
| `service/status.py` | Cluster D minus heartbeat (status_payload, peer_config_warnings, status_payload_enriched, list_models_aggregated) + Cluster B (telemetry/metrics recording) | ~280 |
| `service/swarm_tasks.py` | Cluster L (2046–2246) + `handle_heartbeat`/`_maybe_follow_gateway` (414–458) | ~300 |

Total ≈ 2,500 (growth is module headers/imports). Largest module ~500 — all under the ~600 cap. Public seams that appear: `_RequestPlan` (policy → proxy/messages), the failover invoker contract (`async invoke(backend, plan) -> result | AsyncIterator[str]`), and an accounting hook (proxy → status/telemetry) that F-33's fix can target once instead of five times.

**Sequencing with the in-flight work:** the F-30..F-48 remediation currently editing the tree will change `proxy_messages_stream` (F-33: add success/token accounting, per 09:155-157) and `netllm-sdk-openai/client.py` (F-35). The split must start from *that* landed state, not this HEAD — otherwise the F-33 fix gets orphaned in a deleted method body. Do the mechanical move only after the remediation branch merges; the 647-passing suite (09 baseline) is the safety net F-26 names.

---

## Part 2 — `packages/netllm-cli/src/netllm_cli/main.py` (2,141 lines at HEAD)

### 2.1 Full top-level inventory

**Wiring / shared helpers**
| Item | Lines |
|---|---|
| Imports (note: rich output primitives already live in `netllm_cli.ui`, imported at 52–73) | 1–73 |
| `__version__ = get_version()` | 75 |
| `app = typer.Typer(...)` | 77–82 |
| `_version_callback` | 84–88 |
| `main` (app callback) | 90–101 |
| `_config_path_option` | 104–106 |
| `_require_config` | 108–131 |

**Init / install group**
| Item | Lines |
|---|---|
| `_resolve_init_swarm_mode` | 133–153 |
| `_listen_port_of` | 155–158 |
| `_apply_open_swarm_mode` | 160–165 |
| `_apply_secured_swarm_mode` | 167–176 |
| `_join_command_for` | 178–183 |
| `_print_swarm_summary` | 185–210 |
| `_swarm_next_steps` | 212–233 |
| `_run_init_post_save` | 235–287 |
| `init` | 288–360 |
| `install` | 361–391 |

**Discover / join / swarm-token group**
| Item | Lines |
|---|---|
| `discover` | 392–443 |
| `_normalize_agent_url` | 444–453 |
| `_fetch_join_status` | 455–466 |
| `_validate_join_token` | 468–508 |
| `join` | 509–572 |
| `_apply_swarm_join_listen` | 573–576 |
| `swarm_token` | 577–643 |

**Observability group**
| Item | Lines |
|---|---|
| `models` | 644–788 |
| `peers` | 789–927 |
| `env_shell` | 928–946 |
| `drain` | 1169–1205 |
| `status` | 1206–1300 |

**Serve / lifecycle group**
| Item | Lines |
|---|---|
| `serve` | 947–1168 (largest single command, 222 lines) |
| `start` | 2098–2106 |
| `stop` | 2107–2112 |
| `restart` | 2113–2121 |
| `config_edit` | 2122–2137 |
| `if __name__ == "__main__": app()` | 2139–2141 |

**Diagnostics group**
| Item | Lines |
|---|---|
| `_test_anthropic_agent` | 1301–1368 |
| `test` | 1369–1433 |
| `gateway_enable` | 1434–1453 |
| `doctor` | 1454–1655 (201 lines; the fixed check sequence docs/turnstone-lessons.md:32 cites at main.py:1444) |

**Sub-apps**
| Item | Lines |
|---|---|
| `config_app` + `config_export` / `config_schema_cmd` / `config_import_cmd` | 1656–1710 |
| `cloud_app` + `_cloud_provider_id_or_exit`, `cloud_list`, `cloud_enable`, `cloud_disable`, `cloud_set_key`, `cloud_fallback`, `cloud_test`, `cloud_connect` (lazy `from netllm_cli import oauth_pkce` at 1963) | 1711–2023 |
| `sources_app` + `sources_list`, `sources_toggle` | 2024–2097 |

### 2.2 Coupling between groups

| Shared helper | Call sites | Groups touched |
|---|---|---|
| `_config_path_option` | **24 sites**: 319, 403, 526, 588, 665, 807, 971, 1183, 1212, 1381, 1439, 1460, 1665, 1700, 1736, 1786, 1827, 1849, 1877, 1906, 2013, 2034, 2067, 2127 | every group |
| `_require_config` | 589 (swarm_token), 972 (serve), 1440 (gateway) | 3 groups |
| `_join_command_for` | 190, 223 (init summary) · 598, 641 (swarm_token) | init ↔ join — the only real cross-group helper coupling |
| `_listen_port_of` | 161 (init) · 574 (join) — also the "correct helper" 07:392 tells other files to reuse | init ↔ join |
| `_normalize_agent_url` | 529 (join only) | 1 group |
| `_cloud_provider_id_or_exit` | 1785, 1826, 1848, 1905, 1965 | cloud sub-app only |
| `httpx` module attribute | drain, join, peers, status, cloud_test use it directly; tests patch it *as an attribute of the module* (see 2.3) | cross-group via tests |

Output formatting is **not** a coupling problem: everything (`console`, `print_error`, `print_heading`, `peers_table`, `models_table`, …) is already in `netllm_cli/ui.py` (imported main.py:52–73). No new formatting module is needed.

### 2.3 External usages that constrain the split

Production / packaging:
- `pyproject.toml:12` and `packages/netllm-cli/pyproject.toml:16` — entry point `netllm = "netllm_cli.main:app"` → `netllm_cli.main` must remain importable with an `app` attribute.
- `packages/netllm-cli/src/netllm_cli/__init__.py:3` — `from netllm_cli.main import app`.
- `apps/netllm-mac/Scripts/build.sh:120` — `exec … python3 -S -m netllm_cli.main "$@"` → `main.py` must stay runnable as `-m` (keep the `if __name__ == "__main__"` block).
- `apps/netllm-mac/Sources/AppView/SettingsWindowView.swift:692` — comment-only reference to `netllm_cli.main.sources_toggle` (update the comment, no code constraint).

Tests (patch targets pinned to the `netllm_cli.main` namespace — each breaks if the consuming command moves without repointing):
- `patch("netllm_cli.main.asyncio.run")` — test_doctor_app_context.py:37, test_doctor_supervised_port.py:39, test_serve_quiet_lan.py:36 (doctor, serve)
- `patch("netllm_cli.main.control_socket_path")` — test_doctor_supervised_port.py:30 (serve/doctor path)
- `patch("netllm_cli.main.global_netllm_installed")` / `global_cli_on_path` — test_doctor_app_context.py:34–35
- `patch("netllm_cli.main.mdns_available")` — test_doctor_app_context.py:44, test_doctor_supervised_port.py:43; plus `monkeypatch.setattr(cli_main, "mdns_available", …)` test_doctor_open_lan.py:35
- `monkeypatch.setattr(cli_main, "scan_local_providers", …)` — test_contract.py:84, test_cli_swarm_init.py:22, test_doctor_open_lan.py:24 (init, discover, doctor)
- `patch.object(cli_main.httpx, "Client", …)` — test_cli_drain.py:53/64/76/107, test_cli_swarm_init.py:191 (drain, join)
- Direct helper calls in tests: `cli_main._apply_open_swarm_mode`, `_apply_secured_swarm_mode`, `_listen_port_of`, `_normalize_agent_url`, `_validate_join_token` (test_cli_swarm_init.py et al.) — keep these importable (re-export from `main` or repoint tests)
- **test_version_sync.py:60–63 asserts the literal strings** `"from netllm_core.version import get_version"` and `"__version__ = get_version()"` **appear in `packages/netllm-cli/src/netllm_cli/main.py`** — the residual `main.py` must retain both lines (or the test moves with the code).

### 2.4 Recommended target layout: `netllm_cli/commands/` package

`main.py` survives as pure Typer wiring (~130 lines): imports, `__version__ = get_version()` (test_version_sync constraint), `app` creation, version callback, `app.command()` registrations via `from netllm_cli.commands import …`, `add_typer` for the three sub-apps, and the `__main__` block. Recommended registration style: each command module exposes plain functions; `main.py` registers them (`app.command("init")(init_cmd.init)`) so the entry-point surface stays in one greppable place.

| New module | Contents | Est. size |
|---|---|---|
| `commands/_common.py` | `_config_path_option` (104–106), `_require_config` (108–131), `_normalize_agent_url` (444–453) | ~70 |
| `commands/init_install.py` | 133–391: the 8 init helpers + `init` + `install`; `_listen_port_of` and `_join_command_for` live here and are imported by `join_swarm.py` (explicit seam replacing today's implicit sharing) | ~290 |
| `commands/join_swarm.py` | 392–643: `discover`, `_fetch_join_status`, `_validate_join_token`, `join`, `_apply_swarm_join_listen`, `swarm_token` | ~250 |
| `commands/observe.py` | `models` (644–788), `peers` (789–927), `env_shell` (928–946), `drain` (1169–1205), `status` (1206–1300) | ~450 |
| `commands/serve_lifecycle.py` | `serve` (947–1168), `start`/`stop`/`restart`/`config_edit` (2098–2137) | ~290 |
| `commands/diagnose.py` | `_test_anthropic_agent` (1301–1368), `test` (1369–1433), `gateway_enable` (1434–1453), `doctor` (1454–1655) | ~380 |
| `commands/config_io.py` | config sub-app (1656–1710) — name avoids clashing with existing `netllm_cli/config_json.py` | ~70 |
| `commands/cloud.py` | cloud sub-app (1711–2023) incl. `_cloud_provider_id_or_exit` | ~330 |
| `commands/sources.py` | sources sub-app (2024–2097) | ~90 |

Largest module ~450 — all under the ~600 cap. Test repointing is mechanical but mandatory: the 12 namespace-patch sites in 2.3 must become `netllm_cli.commands.diagnose.mdns_available`, `netllm_cli.commands.observe.httpx`, etc. Do it in the same commit as the move, keyed off the table above; do **not** try to preserve them via re-import tricks in `main.py` (a `main.py` that re-imports `httpx`/`asyncio`/`mdns_available` for patchability would defeat the split's readability purpose and still not intercept the moved call sites).

---

## Part 3 — dashboard.js: include or defer

`packages/netllm-agent/src/netllm_agent/static/dashboard.js` is 2,817 lines at HEAD — the largest first-party source file (09:380), grown +95 lines in ccc1c79, and flagged in F-49 (09:321-331) for hand-mirroring the telemetry contract that `telemetry.py:47` already emits canonically. **Recommendation: defer the structural JS split, but include the narrow F-49 contract slice in this refactor.** A real modularisation of dashboard.js means either introducing ES-module serving or a build step — a different risk class with zero coverage from the 647-test Python suite that F-26 explicitly names as the safety net, and it would double the review surface of an already large mechanical change. However, since `service/status.py` and the telemetry seam are being touched anyway, this is the right moment to do exactly what F-49 prescribes: treat `docs/telemetry-api.md` as normative, delete dashboard.js's client-side key fallbacks/re-derivations (`routerScopeBlock`'s `total_tokens` recomputation), and add the Python-side contract test on the documented key set — shrinking dashboard.js's coupling to the server without restructuring it. File the full JS split as its own follow-up finding-register entry.

---

## Summary of hard constraints (checklist for the implementer)

1. `netllm_agent.service` must keep exporting `AgentService` and `SourceCapacityExceeded` (importers: `__init__.py:4`, `admin.py:23`, `app.py:34`).
2. The 20-method/attr surface app.py consumes (Part 1.3) is the frozen public API of `AgentService`.
3. Decide explicitly on the `netllm_agent.service.scan_local_providers` patch target (25 test sites): package-namespace indirection or one mechanical test repoint — not both, not neither.
4. `netllm_cli.main` must keep `app`, `__version__ = get_version()` + its import line (test_version_sync.py:60–63), and `-m` runnability (build.sh:120; pyproject entry points ×2).
5. Repoint the 12 `netllm_cli.main.*` namespace patch targets and the 5 directly-imported private helpers listed in 2.3 in the same commit as the CLI move.
6. Land after the in-flight F-30..F-48 remediation merges — F-33's fix inside `proxy_messages_stream` (service.py:1904-1910 region) and F-35's sdk change must be in the base, and the F-24 `_run_with_failover` extraction in `service/proxy.py` is what makes the F-33/F-35 divergence class structurally impossible afterwards.