import Foundation

struct NetllmConfigDocument: Codable, Sendable {
    var agent: AgentSection = AgentSection()
    /// Dynamic — schema-driven (docs/config-schema-rewrite-plan.md §5
    /// phase 4, Option A). No typed DiscoverySection/SwarmSection struct
    /// exists anymore; existing hand-tuned views (discoveryTab/swarmTab
    /// in SettingsWindowView) bind through the `Binding<[String:
    /// JSONValue]>` bridging helpers in JSONValue.swift, unchanged in
    /// behavior — only the storage type changed. This also exposes 3
    /// swarm fields (require_token_for_inference, peer_stale_after_s,
    /// rediscover_interval_s) the old typed struct never modeled, via a
    /// generic SchemaFormView slice for genuinely new fields only.
    var discovery: [String: JSONValue] = [:]
    var swarm: [String: JSONValue] = [:]
    var routing: RoutingSection = RoutingSection()
    /// Dynamic — schema-driven (docs/config-schema-rewrite-plan.md §5
    /// phase 4, Option A). Rendered by SchemaFormView against the
    /// `ui` section of ConfigStore.loadSchema(); no typed UiSection
    /// struct exists anymore for this section. Keys/values match
    /// UiConfig's pydantic fields (auto_start_on_launch, log_dir,
    /// check_for_updates_automatically) but nothing here enforces that
    /// at compile time — the schema does, at render/save time.
    var ui: [String: JSONValue] = [:]
    var cloud: CloudSection = CloudSection()

    struct AgentSection: Codable, Sendable {
        var listen: String = "127.0.0.1:11400"
        var role: String = "peer"
        var advertise: Bool = true
        var agent_id: String = ""
        var hostname: String = ""
        var max_concurrency: Int = 0
    }

    struct RoutingPolicy: Codable, Sendable, Identifiable {
        var id: String { name.isEmpty ? "\(model_prefix)-\(api_format ?? "any")" : name }
        var name: String = ""
        var model_prefix: String = ""
        var api_format: String?
        var strategy: String?
        var prefer_provider: String?
        var allow_cloud: Bool = false
        var enabled: Bool = true
        // Scopes the policy to one caller (a `[[routing.sources]]` id).
        // Policies have no identity key, so config_merge rebuilds each row
        // from RoutingPolicy() defaults plus whatever the patch sends -- a
        // field this struct does not carry is therefore not "left alone" on
        // Save, it is ERASED. Omitting `source` silently widened a
        // source-scoped policy to every caller: F-01 reintroduced through the
        // Swift surface. Guarded by tests/conformance/kit_config_surfaces.py.
        var source: String?
    }

    struct RoutingSection: Codable, Sendable {
        var default_strategy: String = "local_first"
        var allow_remote: Bool = true
        // Back-pressure cap applied by every strategy: selection prefers
        // backends with fewer than this many requests in flight. 0 = off.
        var max_in_flight_per_backend: Int = 0
        // Peer-role agents adopt the gateway's advertised default_strategy
        // from heartbeats (runtime only, not persisted); false opts out.
        var follow_gateway: Bool = true
        // local_spillover: serve locally while fewer than this many
        // requests are in flight locally; spill to a LAN peer above it.
        var spillover_max_local_in_flight: Int = 2
        // Health cache: how long a probe result stays fresh, and how many
        // consecutive request failures mark a backend offline.
        var health_ttl_s: Double = 30.0
        // Offline backends are re-probed after this many seconds instead
        // of waiting out the full health TTL.
        var offline_retry_s: Double = 10.0
        var max_backend_failures: Int = 3
        // Upstream connect/read timeouts (F-22, promoted to config in
        // bb3eae0). Both shipped with no control on any surface, which
        // left the 120 s read timeout unraisable without editing source
        // -- the case F-22 called out as most likely to bite.
        var upstream_connect_timeout_s: Double = 5.0
        var upstream_read_timeout_s: Double = 120.0
        // One-shot marker: once the LAN upgrade has run, an explicit
        // user strategy choice is never silently rewritten again.
        var lan_defaults_applied: Bool = false
        var backends: [BackendOverride] = []
        var policies: [RoutingPolicy] = []
        /// Dynamic dict[alias name -> served model IDs] (routing.model_aliases).
        /// Same same-day-added-feature reasoning as model_pools below: no
        /// prior Swift UI existed, so exposing it generically here is pure
        /// addition, not a regression risk to an existing hand-tuned editor.
        var model_aliases: [String: JSONValue] = [:]
        /// Dynamic dict[name -> ModelPool] (docs/config-schema-rewrite-plan.md
        /// §5 phase 4) — a same-day-added feature with no prior Swift UI,
        /// so exposing it generically here is pure addition, not a
        /// regression risk to an existing hand-tuned editor.
        var model_pools: [String: JSONValue] = [:]
        /// Dynamic list[SourceConfig] (docs/cli-source-routing-plan.md
        /// Phase 4b) — known CLI/harness sources with their own routing.
        /// No prior Swift UI existed for this at all; rendered generically
        /// via SchemaFormView per entry, same as model_pools above.
        var sources: [JSONValue] = []
    }

    struct BackendOverride: Codable, Sendable, Identifiable {
        /// SwiftUI list identity. `row_id` when the agent has minted one,
        /// `base_url` only as the fallback for a row this app just added
        /// (no id until the first Save round-trips through the agent).
        /// It used to be `base_url` alone, which meant a ForEach row lost
        /// its identity mid-edit as the user typed into the URL field.
        var id: String { row_id.isEmpty ? base_url : row_id }
        /// The agent's stable opaque row identity. Read-only and never
        /// rendered — but it MUST be encoded back on Save. The agent
        /// matches this row to the stored one by it; without it, correcting
        /// a port typo in `base_url` reads as "delete that row and create a
        /// different one", and the new row arrives with its write-only
        /// `api_key` blank because a client can never send that value back.
        /// Empty means "not minted yet"; the agent mints one and the next
        /// load carries it. Do not bind a control to this.
        var row_id: String = ""
        var base_url: String = ""
        var provider: String = "custom"
        var api_format: String?
        var api_key: String = ""
        var api_key_env: String = ""
        var enabled: Bool = true
        var local: Bool = true
    }

    struct CloudProviderConfig: Codable, Sendable {
        var enabled: Bool = false
        var region: String = ""
        var api_format: String?
        /// Model allowlist (cloud.providers.<id>.models). Empty = every
        /// model the provider serves (live /models probe or the registry's
        /// static catalog) — matches the server's materialization rule.
        var models: [String] = []
        /// Name of the environment variable holding this provider's key,
        /// for operators who keep keys out of config.toml entirely. The
        /// agent resolves it before the registry's own env var.
        var api_key_env: String = ""
        /// Overrides the registry endpoint for this region/api_format —
        /// proxies, gateways and self-hosted compatible endpoints.
        var base_url: String = ""
    }

    struct CloudSection: Codable, Sendable {
        var enabled: Bool = true
        var fallback: String = "cloud"
        var fallback_enabled: Bool = true
        // Keyed by provider id (moonshot, zai, openai, anthropic, openrouter).
        // Keys themselves are Keychain-managed, not stored here — see
        // KeychainStore.Account and PythonRuntime.injectCloudAPIKeys.
        var providers: [String: CloudProviderConfig] = [:]
    }

    /// Menubar projection of schema `ui` keys — `document.ui` stays dynamic.
    struct UiSection: Codable, Sendable {
        var auto_start_on_launch: Bool = true
        var log_dir: String = ""
        var check_for_updates_automatically: Bool = true
        var model_favorites: [String] = []
        var menubar_show_cpu: Bool = false
        var menubar_show_gpu: Bool = false
        var menubar_show_mem: Bool = false
        var menubar_show_live: Bool = false
        var menubar_merge_gauges: Bool = false
        var menubar_models_favorites_only: Bool = false

        init(ui: [String: JSONValue] = [:]) {
            auto_start_on_launch = ui.bool("auto_start_on_launch", default: true)
            log_dir = ui.string("log_dir")
            check_for_updates_automatically = ui.bool(
                "check_for_updates_automatically",
                default: true
            )
            model_favorites = ui.stringArray("model_favorites")
            menubar_show_cpu = ui.bool("menubar_show_cpu")
            menubar_show_gpu = ui.bool("menubar_show_gpu")
            menubar_show_mem = ui.bool("menubar_show_mem")
            menubar_show_live = ui.bool("menubar_show_live")
            menubar_merge_gauges = ui.bool("menubar_merge_gauges")
            menubar_models_favorites_only = ui.bool("menubar_models_favorites_only")
        }
    }

    var bindHost: String {
        listenParts.host
    }

    var port: Int {
        listenParts.port
    }

    private var listenParts: (host: String, port: Int) {
        let parts = agent.listen.split(separator: ":", maxSplits: 1)
        let host = parts.first.map(String.init) ?? "127.0.0.1"
        let port = parts.count > 1 ? Int(parts[1]) ?? 11400 : 11400
        return (host, port)
    }

    mutating func setListen(host: String, port: Int) {
        agent.listen = "\(host):\(port)"
    }

    mutating func setLanMode(_ enabled: Bool, port: Int) {
        setListen(host: enabled ? "0.0.0.0" : "127.0.0.1", port: port)
    }

    var isLanMode: Bool {
        bindHost == "0.0.0.0"
    }

    /// Mesh routing/discovery defaults when listening on the LAN (no token minting).
    mutating func applyLanMeshDefaults() {
        guard isLanMode else { return }
        if !routing.lan_defaults_applied {
            if routing.default_strategy == "local_first" {
                routing.default_strategy = "local_spillover"
            }
            routing.lan_defaults_applied = true
        }
        if !swarm.bool("subnet_scan") {
            swarm["subnet_scan"] = .bool(true)
        }
    }
}

struct AgentVersionPayload: Sendable {
    var version: String = ""
    var platform: String = ""
    var installMethod: String = ""
    var openaiSDK: String = ""
    var anthropicSDK: String = ""
}

struct AgentStatusPayload: Sendable {
    var agentId: String = ""
    var hostname: String = ""
    var role: String = "peer"
    var listenURL: String = ""
    var routingStrategy: String = ""
    var draining: Bool = false
    var reachable: Bool = true
    var sourceRequests: [String: Int] = [:]
    var scenarioRequests: [String: Int] = [:]
    var capacityRejections: Int = 0
    var shardlessFallbacks: Int = 0
    var peerWarnings: [String] = []
    var backends: [BackendStatus] = []
    var peers: [PeerStatus] = []
}

struct BackendStatus: Identifiable, Sendable {
    var id: String { baseURL }
    var provider: String
    var baseURL: String
    var local: Bool
    var enabled: Bool
    var health: String
    var modelCount: Int
    var models: [String]
    var inFlight: Int
    /// Server-side Backend.id ("omlx@http://…", "peer:<agent-id>", …) —
    /// one of the ref forms a model pool's `hosts` list accepts.
    var backendId: String = ""
    /// Owning agent — groups peer backends by machine and matches the
    /// bare-agent-id pool host ref. Empty on agents older than this field.
    var agentId: String = ""
    /// Cloud provider id ("openai", "anthropic", …) when this row was
    /// materialized from [cloud.providers.<id>]; empty for local/peer rows.
    var cloudProvider: String = ""
}

/// One cloud provider's model catalog from
/// GET /netllm/v1/cloud/providers/{id}/models — the full list of models
/// the provider offers (live probe or static registry fallback),
/// independent of the configured allowlist.
struct CloudModelCatalog: Sendable {
    var source: String
    var status: String
    var detail: String?
    var models: [String]
}

/// One provider's credential-verification state, as the agent reports it
/// (netllm_core.cloud_verification.verification_state — carried on
/// GET /netllm/v1/config and returned by POST .../verify).
///
/// Every field is the server's answer, including `blocker`, the sentence
/// shown to the user, and `canEnable`, the write-path gate's own verdict.
/// Nothing here is derived on this side on purpose: the same rule is
/// enforced when config is saved, and a Swift re-derivation would be a
/// second rule that drifts from the one that decides.
struct CloudVerification: Sendable, Equatable {
    var status: String
    var ok: Bool
    var blocker: String
    var detail: String
    var checkedAt: String
    var canEnable: Bool

    static func from(_ json: [String: Any]) -> CloudVerification {
        CloudVerification(
            status: json["status"] as? String ?? "",
            ok: json["ok"] as? Bool ?? false,
            blocker: json["blocker"] as? String ?? "",
            detail: json["detail"] as? String ?? "",
            checkedAt: json["checked_at"] as? String ?? "",
            // Absent on an agent too old to know about verification: no
            // verdict means no gate, and refusing every provider because the
            // agent is old would be a worse answer than allowing them.
            canEnable: json["can_enable"] as? Bool ?? true
        )
    }
}

/// `reachable_at[].kind` — how an address on another machine relates to the
/// person reading it (`netllm_discovery.lan.ADDRESS_KINDS`).
///
/// A wildcard-bound agent answers on every address its host has, and on a
/// machine running Docker most of those are bridge gateways: real, but only
/// dialable from a container on that same host. The agent classifies, because
/// only it can see its own interface names — 10.0.0.29 and 172.17.0.1 are
/// both RFC1918 and nothing here can tell them apart. This mirrors the wire
/// order rather than deciding it; an unrecognised kind sorts last and is
/// shown verbatim, the same convention `discoveredVia` already uses.
enum PeerAddressKind {
    static let ordered = ["lan", "vpn", "container", "link_local", "loopback"]

    /// Absent ranks with the LAN — an address an older agent never classified
    /// must not be demoted for that; an unrecognised name ranks last.
    static func rank(_ kind: String) -> Int {
        if kind.isEmpty { return 0 }
        return ordered.firstIndex(of: kind) ?? ordered.count
    }

    static func label(_ kind: String) -> String {
        switch kind {
        case "", "lan": return ""
        case "vpn": return "over VPN"
        case "container": return "from containers"
        case "link_local": return "link-local, no DHCP lease"
        case "loopback": return "same machine only"
        default: return kind.replacingOccurrences(of: "_", with: " ")
        }
    }

    /// `alsoReachableAt` ordered by usefulness. Stable: equal ranks keep wire
    /// order, so a 2s poll does not reshuffle the line under the cursor.
    static func sorted(_ urls: [String], kinds: [String: String]) -> [String] {
        urls.enumerated()
            .sorted { lhs, rhs in
                let l = rank(kinds[lhs.element] ?? "")
                let r = rank(kinds[rhs.element] ?? "")
                return l == r ? lhs.offset < rhs.offset : l < r
            }
            .map(\.element)
    }
}

struct PeerStatus: Identifiable, Sendable {
    var id: String { agentId }
    var agentId: String
    var listenURL: String
    var role: String
    var hostname: String
    /// `PeerRecord.discovered_via` — `mdns` / `subnet_scan` / `static` /
    /// `heartbeat` / `join` (UI-4a). This agent's own knowledge, not
    /// something the peer claims about itself. Empty on an older agent, and
    /// on a `peers-scan` result that has no registry row to answer from.
    var discoveredVia: String = ""
    /// Alternate LAN URLs the peer also answers on (wildcard binds only).
    var alsoReachableAt: [String] = []
    /// `reachable_at` flattened to url -> kind (UI-4a). A lookup, not a
    /// replacement for `alsoReachableAt`: this agent also observes addresses
    /// the peer never advertised, and those stay in the list unclassified.
    /// Empty for a peer whose build predates the key.
    var addressKinds: [String: String] = [:]
}

struct DiscoverProvider: Identifiable, Sendable {
    var id: String
    var name: String
    var baseURL: String
    var status: String
    var models: [String]
}

struct ModelRow: Identifiable, Sendable {
    var id: String
    var model: String
    var provider: String
    var host: String
    var scope: String
}

/// A failing, error-severity doctor finding — the pre-UI-6 `issues[]` shape,
/// which the agent still derives from `checks[]` and which is the only thing
/// an agent older than UI-6 sends. See `DoctorCheck` for the structured form.
struct DoctorIssue: Identifiable, Sendable {
    /// `title` alone is NOT unique: a check that fans out (one row per
    /// backend, one per deprecated key) repeats its title, and duplicate
    /// ForEach ids make SwiftUI silently drop the later rows. `ordinal` is
    /// the payload's own array position, which is unique by construction and
    /// stable for as long as the list is.
    var id: String { "\(ordinal)|\(title)" }
    var ordinal: Int = 0
    var title: String
    var fix: String
}

/// One row of `checks[]` from `netllm doctor --json` / `/netllm/v1/doctor`.
///
/// Mirrors `netllm_core.doctor_checks.doctor_check`. The point of the
/// structured form is that a client keys a fix on a stable `id` instead of
/// regex-matching prose, and can report what *passed* rather than only what
/// broke.
struct DoctorCheck: Identifiable, Sendable {
    /// `(checkID, subject)` is the unique key the agent guarantees;
    /// `checkID` alone is what a fix action keys on, so it survives fan-out.
    var id: String { subject.isEmpty ? checkID : "\(checkID)|\(subject)" }
    /// Stable dotted id, e.g. `config.deprecated_keys`. Never match wording.
    var checkID: String
    /// Distinguishes rows of a check that fans out over several things (one
    /// backend per row). Empty for a check that emits a single row.
    var subject: String = ""
    var title: String
    var ok: Bool
    /// `info` whenever `ok` — the agent rewrites a passing row's severity, so
    /// this never describes a passing check's hypothetical failure. Otherwise
    /// `error` (a real problem; clears top-level `ok`) or `warn` (advisory).
    var severity: String = "error"
    var detail: String = ""
    var fix: String = ""
    /// `action.kind` from the closed set in `DOCTOR_ACTION_KINDS`:
    /// `config_patch`, `admin_post`, `navigate`, `none`. Modelled but not yet
    /// executed by this app — the patch/route body is deliberately not
    /// carried here until something acts on it.
    var actionKind: String = "none"

    var isFailure: Bool { !ok && severity == "error" }
    var isAdvisory: Bool { !ok && severity == "warn" }
}
