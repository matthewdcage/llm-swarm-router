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
    }

    struct RoutingSection: Codable, Sendable {
        var default_strategy: String = "local_first"
        var allow_remote: Bool = true
        var require_same_model_for_shard: Bool = true
        // One-shot marker: once the LAN upgrade has run, an explicit
        // user strategy choice is never silently rewritten again.
        var lan_defaults_applied: Bool = false
        var backends: [BackendOverride] = []
        var policies: [RoutingPolicy] = []
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
        var id: String { base_url }
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

struct PeerStatus: Identifiable, Sendable {
    var id: String { agentId }
    var agentId: String
    var listenURL: String
    var role: String
    var hostname: String
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

struct DoctorIssue: Identifiable, Sendable {
    var id: String { title }
    var title: String
    var fix: String
}
