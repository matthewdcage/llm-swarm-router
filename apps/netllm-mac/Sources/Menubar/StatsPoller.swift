import Foundation

struct BackendSnapshot: Sendable, Identifiable {
    /// `Backend.id` when the agent sends one; base URL is the pre-F-59
    /// fallback so older agents keep a stable ForEach identity.
    var id: String { backendID.isEmpty ? baseURL : backendID }
    var backendID: String = ""
    var provider: String
    var baseURL: String
    var health: String
    var modelCount: Int
    var isLocal: Bool = true
    var inFlight: Int = 0
    /// `BackendHealth.detail` — the probe's own words when it failed.
    var detail: String = ""
    /// `BackendHealth.last_check_epoch_s` — wall clock of the last probe, so
    /// a client can age it without subtracting the agent's monotonic clock
    /// from its own. 0 means never probed in this process, which is a
    /// different statement from "probed and found nothing".
    var lastCheckEpochS: Double = 0

    var isOnline: Bool { health == "online" }

    /// Peer rows are `peer:<agent_id>`; the node list shows them from
    /// `status.peers[]` instead, so it filters these out.
    var isPeerRow: Bool { backendID.hasPrefix("peer:") }
}

/// One provider a peer advertises in its heartbeat (`status.peers[].providers`).
/// Never carries model names — the body is gossiped to every peer.
struct PeerProviderSnapshot: Sendable, Identifiable {
    var id: String
    var provider: String = ""
    var modelCount: Int = 0
}

/// One row of `status.peers[]` (see `SwarmRegistry.all_peer_urls`).
struct PeerSnapshot: Sendable, Identifiable {
    var id: String { agentID.isEmpty ? listenURL : agentID }
    var agentID: String = ""
    var hostname: String = ""
    var listenURL: String = ""
    var role: String = "peer"
    /// `PeerRecord.discovered_via` — `mdns` / `subnet_scan` / `static` /
    /// `heartbeat` / `join`. Receiver-side knowledge, deliberately not in the
    /// heartbeat body (the sender cannot know how it was found). Empty on an
    /// agent that predates UI-4a.
    var discoveredVia: String = ""
    /// Alternate LAN URLs this peer also answers on (wildcard binds only).
    var alsoReachableAt: [String] = []
    /// What the peer serves, without model names. Empty for a peer whose
    /// build predates UI-4a — which is not the same as "serves nothing".
    var providers: [PeerProviderSnapshot] = []

    var displayName: String {
        if !hostname.isEmpty { return hostname }
        if !agentID.isEmpty { return agentID }
        return listenURL
    }

    var isGateway: Bool { role == "gateway" }
}

struct StatsSnapshot: Sendable {
    var backendCount: Int = 0
    var peerCount: Int = 0
    var role: String = "peer"
    var agentID: String = ""
    var hostname: String = ""
    var modelsPreview: String = ""
    var backends: [BackendSnapshot] = []
    var peers: [PeerSnapshot] = []
    var onlineBackendCount: Int = 0
    var omlxAdminURL: String?
    var omlxLoadedModel: String?
    var cloudEnabled: Bool = false
    var cloudFallback: String = "cloud"
    var cloudEnabledProviders: [String] = []
    /// `status.routed_requests` — cumulative since agent start, keyed by
    /// backend id (`peer:<agent_id>` for peer rows). Not a live rate.
    var routedRequests: [String: Int] = [:]
    /// `status.discovery.last_scan_at` — epoch seconds of the last provider
    /// scan, or nil when it has not run in this process. nil is not 0: "never
    /// ran" and "ran and found nothing" must stay distinguishable.
    var lastScanAt: Double?
    /// `status.discovery.last_peer_scan_at`, same rule.
    var lastPeerScanAt: Double?
    var routingStrategy: String = ""
    var draining: Bool = false
    /// `status.reachable` — false when loopback-only and not LAN-routable.
    var reachable: Bool = true
    var sourceRequests: [String: Int] = [:]
    var scenarioRequests: [String: Int] = [:]
    var capacityRejections: Int = 0
    var shardlessFallbacks: Int = 0
    var peerWarnings: [String] = []

    var isGateway: Bool { role == "gateway" }

    /// Explicitly probed-and-failed only. `unknown` (never probed yet)
    /// must not raise a degraded card — that would alarm on startup.
    var offlineBackends: [BackendSnapshot] {
        backends.filter { $0.health == "offline" }
    }

    /// The peer this machine is following, when one advertises itself as
    /// the gateway. Nil for a solo agent or a mesh with no gateway peer.
    var gatewayPeer: PeerSnapshot? { peers.first { $0.isGateway } }

    var routedRequestTotal: Int { routedRequests.values.reduce(0, +) }

    /// Share of `routed_requests` for one backend id, or nil when the
    /// agent has routed nothing yet (no denominator — do not guess).
    func routedShare(forBackendID backendID: String) -> Double? {
        let total = routedRequestTotal
        guard total > 0 else { return nil }
        return Double(routedRequests[backendID] ?? 0) / Double(total)
    }
}

@MainActor
final class StatsPoller {
    private let baseURL: URL
    private var task: Task<Void, Never>?
    private(set) var snapshot = StatsSnapshot()
    var onUpdate: (() -> Void)?

    init(host: String, port: Int) {
        baseURL = URL(string: "http://\(host):\(port)")!
    }

    func start() {
        stop()
        task = Task { [weak self] in
            while !Task.isCancelled {
                await self?.poll()
                try? await Task.sleep(for: .seconds(1))
            }
        }
    }

    func stop() {
        task?.cancel()
        task = nil
    }

    func pollOnce() {
        Task { await poll() }
    }

    private func poll() async {
        var snap = StatsSnapshot()
        if let status = await fetchJSON(path: "/netllm/v1/status") {
            snap.role = status["role"] as? String ?? "peer"
            snap.routingStrategy = status["routing_strategy"] as? String ?? ""
            snap.draining = status["draining"] as? Bool ?? false
            snap.reachable = status["reachable"] as? Bool ?? true
            snap.agentID = status["agent_id"] as? String ?? ""
            snap.hostname = status["hostname"] as? String ?? ""
            snap.capacityRejections = jsonInt(status["capacity_rejections"])
            snap.shardlessFallbacks = jsonInt(status["shardless_fallbacks"])
            snap.peerWarnings = status["peer_warnings"] as? [String] ?? []
            if let sources = status["source_requests"] as? [String: Any] {
                var counts: [String: Int] = [:]
                for (key, value) in sources {
                    counts[key] = jsonInt(value)
                }
                snap.sourceRequests = counts
            }
            if let scenarios = status["scenario_requests"] as? [String: Any] {
                var counts: [String: Int] = [:]
                for (key, value) in scenarios {
                    counts[key] = jsonInt(value)
                }
                snap.scenarioRequests = counts
            }
            snap.omlxAdminURL = status["omlx_admin_url"] as? String
            if let omlxStats = status["omlx_stats"] as? [String: Any] {
                snap.omlxLoadedModel = omlxStats["primary_loaded_model"] as? String
            }
            if let backends = status["backends"] as? [[String: Any]] {
                snap.backends = backends.map { row in
                    let health = row["health"] as? [String: Any] ?? [:]
                    let models = health["models"] as? [String] ?? []
                    let modelCount = max(jsonInt(health["model_count"]), models.count)
                    return BackendSnapshot(
                        backendID: row["id"] as? String ?? "",
                        provider: row["provider"] as? String ?? "?",
                        baseURL: row["base_url"] as? String ?? "",
                        health: health["status"] as? String ?? "unknown",
                        modelCount: modelCount,
                        isLocal: row["local"] as? Bool ?? true,
                        inFlight: jsonInt(row["in_flight"]),
                        detail: health["detail"] as? String ?? "",
                        lastCheckEpochS: jsonDouble(health["last_check_epoch_s"])
                    )
                }
                snap.backendCount = snap.backends.count
                snap.onlineBackendCount = snap.backends.filter { $0.isOnline }.count
            }
            if let peers = status["peers"] as? [[String: Any]] {
                snap.peerCount = peers.count
                snap.peers = peers.map { row in
                    PeerSnapshot(
                        agentID: row["agent_id"] as? String ?? "",
                        hostname: row["hostname"] as? String ?? "",
                        listenURL: row["listen_url"] as? String ?? "",
                        role: row["role"] as? String ?? "peer",
                        discoveredVia: row["discovered_via"] as? String ?? "",
                        alsoReachableAt: row["also_reachable_at"] as? [String] ?? [],
                        providers: parsePeerProviders(row["providers"])
                    )
                }
            }
            if let discovery = status["discovery"] as? [String: Any] {
                snap.lastScanAt = jsonOptionalDouble(discovery["last_scan_at"])
                snap.lastPeerScanAt = jsonOptionalDouble(discovery["last_peer_scan_at"])
            }
            if let routed = status["routed_requests"] as? [String: Any] {
                var counts: [String: Int] = [:]
                for (key, value) in routed {
                    counts[key] = jsonInt(value)
                }
                snap.routedRequests = counts
            }
            if let cloud = status["cloud"] as? [String: Any] {
                snap.cloudEnabled = cloud["enabled"] as? Bool ?? false
                snap.cloudFallback = cloud["fallback"] as? String ?? "cloud"
                snap.cloudEnabledProviders = cloud["enabled_providers"] as? [String] ?? []
            }
        }
        if let models = await fetchJSON(path: "/v1/models") {
            if let data = models["data"] as? [[String: Any]] {
                let names = data.prefix(3).compactMap { $0["id"] as? String }
                snap.modelsPreview = names.joined(separator: ", ")
            }
        }
        snapshot = snap
        onUpdate?()
    }

    private func fetchJSON(path: String) async -> [String: Any]? {
        guard let url = AgentHTTP.url(base: baseURL, path: path) else { return nil }
        var request = URLRequest(url: url)
        request.timeoutInterval = 2
        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard (response as? HTTPURLResponse)?.statusCode == 200 else { return nil }
            return try JSONSerialization.jsonObject(with: data) as? [String: Any]
        } catch {
            return nil
        }
    }

    private func jsonInt(_ value: Any?) -> Int {
        if let value = value as? Int { return value }
        if let value = value as? Double { return Int(value) }
        if let value = value as? NSNumber { return value.intValue }
        return 0
    }

    private func jsonDouble(_ value: Any?) -> Double {
        jsonOptionalDouble(value) ?? 0
    }

    /// Keeps a JSON `null` distinct from 0. The discovery clocks are null
    /// until the pass has run, and epoch 0 is a real (if absurd) instant.
    private func jsonOptionalDouble(_ value: Any?) -> Double? {
        if value == nil || value is NSNull { return nil }
        if let value = value as? Double { return value }
        if let value = value as? Int { return Double(value) }
        if let value = value as? NSNumber { return value.doubleValue }
        return nil
    }

    private func parsePeerProviders(_ value: Any?) -> [PeerProviderSnapshot] {
        guard let rows = value as? [[String: Any]] else { return [] }
        return rows.compactMap { row in
            guard let id = row["id"] as? String, !id.isEmpty else { return nil }
            return PeerProviderSnapshot(
                id: id,
                provider: row["provider"] as? String ?? "",
                modelCount: jsonInt(row["model_count"])
            )
        }
    }
}
