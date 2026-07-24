import AppKit
import Foundation

enum AgentAPI {
    static func version(baseURL: URL) async -> AgentVersionPayload? {
        guard let json = await fetchJSON(baseURL: baseURL, path: "/netllm/v1/version") else {
            return nil
        }
        let sdk = json["sdk_versions"] as? [String: Any] ?? [:]
        return AgentVersionPayload(
            version: json["version"] as? String ?? "",
            platform: json["platform"] as? String ?? "",
            installMethod: json["install_method"] as? String ?? "",
            openaiSDK: sdk["openai"] as? String ?? "",
            anthropicSDK: sdk["anthropic"] as? String ?? ""
        )
    }

    static func status(baseURL: URL) async -> AgentStatusPayload? {
        guard let json = await fetchJSON(baseURL: baseURL, path: "/netllm/v1/status") else { return nil }
        let backends = (json["backends"] as? [[String: Any]] ?? []).map(parseBackend)
        let peers = (json["peers"] as? [[String: Any]] ?? []).map(parsePeer)
        return AgentStatusPayload(
            agentId: json["agent_id"] as? String ?? "",
            hostname: json["hostname"] as? String ?? "",
            role: json["role"] as? String ?? "peer",
            listenURL: json["listen_url"] as? String ?? "",
            routingStrategy: json["routing_strategy"] as? String ?? "",
            backends: backends,
            peers: peers
        )
    }

    static func models(baseURL: URL) async -> [ModelRow] {
        guard let json = await fetchJSON(baseURL: baseURL, path: "/v1/models") else { return [] }
        return parseModelRows(from: json["data"] as? [[String: Any]] ?? [])
    }

    static func modelsFromStatus(_ status: AgentStatusPayload) -> [ModelRow] {
        var seen = Set<String>()
        var rows: [ModelRow] = []
        for backend in status.backends where backend.health == "online" {
            for model in backend.models where seen.insert(model).inserted {
                rows.append(
                    ModelRow(
                        id: model,
                        model: model,
                        provider: backend.provider,
                        host: backend.baseURL,
                        scope: "routed"
                    )
                )
            }
        }
        return rows.sorted { $0.model.localizedCaseInsensitiveCompare($1.model) == .orderedAscending }
    }

    private static func parseModelRows(from data: [[String: Any]]) -> [ModelRow] {
        data.compactMap { item in
            guard let id = item["id"] as? String else { return nil }
            return ModelRow(
                id: id,
                model: id,
                provider: item["owned_by"] as? String ?? "?",
                host: "agent",
                scope: "routed"
            )
        }
    }

    static func logs(baseURL: URL, tail: Int = 200) async -> AgentLogsPayload? {
        guard let json = await fetchJSON(
            baseURL: baseURL,
            path: "/netllm/v1/logs?tail=\(tail)"
        ) else {
            return nil
        }
        let lines = json["tail"] as? [String] ?? []
        return AgentLogsPayload(
            logDir: json["log_dir"] as? String ?? "",
            logFile: json["log_file"] as? String ?? "",
            exists: json["exists"] as? Bool ?? false,
            sizeBytes: parseInt(json["size_bytes"]),
            tail: lines,
            truncated: json["truncated"] as? Bool ?? false
        )
    }

    /// Cloud provider registry (display metadata) — single source of
    /// truth served by the agent (admin.cloud_provider_registry_payload)
    /// so this data never has to be hand-mirrored in Swift. Falls back to
    /// SettingsViewModel.cloudProviders (bootstrap defaults) when the
    /// agent is unreachable.
    static func cloudProviderRegistry(baseURL: URL) async -> [CloudProviderInfo]? {
        guard let json = await fetchJSON(baseURL: baseURL, path: "/netllm/v1/cloud/providers")
        else {
            return nil
        }
        let rows = json["providers"] as? [[String: Any]] ?? []
        guard !rows.isEmpty else { return nil }
        return rows.compactMap { row in
            guard let id = row["id"] as? String else { return nil }
            return CloudProviderInfo(
                id: id,
                displayName: row["display_name"] as? String ?? id,
                notes: row["notes"] as? String ?? "",
                regions: row["regions"] as? [String] ?? ["global"],
                keychainAccount: KeychainStore.accountForCloudProvider(id)
            )
        }
    }

    /// Full model catalog for one cloud provider (live probe with the
    /// configured key, static registry fallback) — feeds the allowlist
    /// checklist in CloudSettingsView. Independent of the allowlist by
    /// design: the materialized backend's health.models IS the allowlist
    /// once one is set, so status can't show what else could be enabled.
    static func cloudProviderModels(baseURL: URL, providerID: String) async -> CloudModelCatalog? {
        guard let json = await fetchJSON(
            baseURL: baseURL,
            path: "/netllm/v1/cloud/providers/\(providerID)/models",
            timeout: 15
        ) else {
            return nil
        }
        return CloudModelCatalog(
            source: json["source"] as? String ?? "static",
            status: json["status"] as? String ?? "unknown",
            detail: json["detail"] as? String,
            models: json["models"] as? [String] ?? []
        )
    }

    /// Known-harness registry merged with configured routing.sources state
    /// and live PATH detection (admin.harness_registry_payload,
    /// docs/cli-source-routing-plan.md Phase 4c/4d). `nil` on an older
    /// agent that predates this endpoint (404) or when unreachable — the
    /// badge/quick-add UI simply doesn't render, same graceful-degrade
    /// pattern as cloudProviderRegistry.
    static func harnesses(baseURL: URL) async -> [HarnessInfo]? {
        guard let json = await fetchJSON(baseURL: baseURL, path: "/netllm/v1/harnesses")
        else {
            return nil
        }
        let rows = json["harnesses"] as? [[String: Any]] ?? []
        return rows.compactMap { row in
            guard let id = row["id"] as? String else { return nil }
            return HarnessInfo(
                id: id,
                displayName: row["display_name"] as? String ?? id,
                configured: row["configured"] as? Bool ?? false,
                enabled: row["enabled"] as? Bool ?? false,
                detected: row["detected"] as? Bool ?? false,
                installHint: row["install_hint"] as? String ?? "",
                docsURL: row["docs_url"] as? String,
                iconPath: row["icon_url"] as? String
            )
        }
    }

    /// Fetches one harness's SVG icon (served from the static mount, see
    /// `harnesses` above) and rasterizes it via NSImage — macOS has
    /// supported loading SVG data directly since Catalina. Callers should
    /// cache the result (SettingsViewModel.harnessIcon) rather than
    /// refetching every poll cycle; the icon set is effectively static.
    static func harnessIcon(baseURL: URL, path: String) async -> NSImage? {
        var request = URLRequest(url: baseURL.appendingPathComponent(path))
        request.timeoutInterval = 5
        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard (response as? HTTPURLResponse)?.statusCode == 200 else { return nil }
            return NSImage(data: data)
        } catch {
            return nil
        }
    }

    static func isReachable(baseURL: URL) async -> Bool {
        var request = URLRequest(url: baseURL.appendingPathComponent("/health"))
        request.timeoutInterval = 2
        do {
            let (_, response) = try await URLSession.shared.data(for: request)
            return (response as? HTTPURLResponse)?.statusCode == 200
        } catch {
            return false
        }
    }

    /// Subnet-scan for LAN agents (same as `netllm peers --subnet-scan`).
    static func peersScan(baseURL: URL, save: Bool = false) async -> (peers: [PeerStatus], warnings: String)? {
        var components = URLComponents(
            url: baseURL.appendingPathComponent("/netllm/v1/admin/peers-scan"),
            resolvingAgainstBaseURL: false
        )
        if save {
            components?.queryItems = [URLQueryItem(name: "save", value: "true")]
        }
        guard let url = components?.url else { return nil }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 30
        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard (response as? HTTPURLResponse)?.statusCode == 200 else { return nil }
            guard let json = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                return nil
            }
            let rows = json["peers"] as? [[String: Any]] ?? []
            let peers = rows.map(parsePeer)
            let warnings = (json["warnings"] as? [String] ?? []).joined(separator: " ")
            return (peers, warnings)
        } catch {
            return nil
        }
    }

    private static func parseBackend(_ dict: [String: Any]) -> BackendStatus {
        let health = dict["health"] as? [String: Any] ?? [:]
        let models = health["models"] as? [String] ?? []
        let modelCount = max(parseInt(health["model_count"]), models.count)
        return BackendStatus(
            provider: dict["provider"] as? String ?? "",
            baseURL: dict["base_url"] as? String ?? "",
            local: dict["local"] as? Bool ?? true,
            enabled: dict["enabled"] as? Bool ?? true,
            health: health["status"] as? String ?? "unknown",
            modelCount: modelCount,
            models: models,
            inFlight: parseInt(dict["in_flight"]),
            backendId: dict["id"] as? String ?? "",
            agentId: dict["agent_id"] as? String ?? "",
            cloudProvider: dict["cloud_provider"] as? String ?? ""
        )
    }

    private static func parseInt(_ value: Any?) -> Int {
        if let value = value as? Int { return value }
        if let value = value as? Double { return Int(value) }
        if let value = value as? NSNumber { return value.intValue }
        return 0
    }

    private static func parsePeer(_ dict: [String: Any]) -> PeerStatus {
        PeerStatus(
            agentId: dict["agent_id"] as? String ?? "",
            listenURL: dict["listen_url"] as? String ?? "",
            role: dict["role"] as? String ?? "peer",
            hostname: dict["hostname"] as? String ?? ""
        )
    }

    private static func fetchJSON(
        baseURL: URL, path: String, timeout: TimeInterval = 5
    ) async -> [String: Any]? {
        var request = URLRequest(url: baseURL.appendingPathComponent(path))
        request.timeoutInterval = timeout
        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard (response as? HTTPURLResponse)?.statusCode == 200 else { return nil }
            return try JSONSerialization.jsonObject(with: data) as? [String: Any]
        } catch {
            return nil
        }
    }
}

/// One row from GET /netllm/v1/harnesses — a known harness (registry
/// metadata) merged with this agent's routing.sources configuration state
/// and live PATH detection. See AgentAPI.harnesses.
struct HarnessInfo: Identifiable, Hashable {
    var id: String
    var displayName: String
    var configured: Bool
    var enabled: Bool
    var detected: Bool
    var installHint: String
    var docsURL: String?
    /// Server-relative path (e.g. "/ui/icons/harnesses/codex.svg") — fetch
    /// via AgentAPI.harnessIcon and cache; see SettingsViewModel.harnessIcon.
    var iconPath: String?
}
