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
            inFlight: parseInt(dict["in_flight"])
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

    private static func fetchJSON(baseURL: URL, path: String) async -> [String: Any]? {
        var request = URLRequest(url: baseURL.appendingPathComponent(path))
        request.timeoutInterval = 5
        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard (response as? HTTPURLResponse)?.statusCode == 200 else { return nil }
            return try JSONSerialization.jsonObject(with: data) as? [String: Any]
        } catch {
            return nil
        }
    }
}
