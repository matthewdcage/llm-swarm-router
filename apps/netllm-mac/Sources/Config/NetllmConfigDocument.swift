import Foundation

struct NetllmConfigDocument: Codable, Sendable {
    var agent: AgentSection = AgentSection()
    var discovery: DiscoverySection = DiscoverySection()
    var swarm: SwarmSection = SwarmSection()
    var routing: RoutingSection = RoutingSection()
    var ui: UiSection = UiSection()
    var cloud: CloudSection = CloudSection()

    struct AgentSection: Codable, Sendable {
        var listen: String = "127.0.0.1:11400"
        var role: String = "peer"
        var advertise: Bool = true
        var agent_id: String = ""
        var hostname: String = ""
    }

    struct DiscoverySection: Codable, Sendable {
        var providers: [String] = ["omlx", "ollama", "lmstudio", "vllm"]
        var custom_endpoints: [String] = []
        /// Per-machine base URLs (e.g. oMLX on :8088). Empty = auto-scan default ports.
        var provider_urls: [String: [String]] = [:]
    }

    struct SwarmSection: Codable, Sendable {
        var peers: [String] = []
        var mdns: Bool = true
        var subnet_scan: Bool = false
        var subnet_cidrs: [String] = []
        var cluster_token: String = ""
        var heartbeat_interval_s: Double = 10.0
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

    struct UiSection: Codable, Sendable {
        var auto_start_on_launch: Bool = true
        var log_dir: String = ""
        var check_for_updates_automatically: Bool = true
    }

    struct CloudProviderConfig: Codable, Sendable {
        var enabled: Bool = false
        var region: String = ""
        var api_format: String?
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
        if !swarm.subnet_scan {
            swarm.subnet_scan = true
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
