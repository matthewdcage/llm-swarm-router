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

    static func status(
        baseURL: URL,
        forceScan: Bool = false,
        forceProbe: Bool = false,
        forceProbePeers: Bool = false
    ) async -> AgentStatusPayload? {
        var path = "/netllm/v1/status"
        var query: [String] = []
        if forceScan { query.append("scan=1") }
        if forceProbe { query.append("probe=1") }
        if forceProbePeers { query.append("probe_peers=1") }
        if !query.isEmpty { path += "?" + query.joined(separator: "&") }
        let timeout: TimeInterval = (forceScan || forceProbe || forceProbePeers) ? 120 : 15
        guard let json = await fetchJSON(baseURL: baseURL, path: path, timeout: timeout)
        else { return nil }
        let backends = (json["backends"] as? [[String: Any]] ?? []).map(parseBackend)
        let peers = (json["peers"] as? [[String: Any]] ?? []).map(parsePeer)
        var sourceRequests: [String: Int] = [:]
        if let sources = json["source_requests"] as? [String: Any] {
            for (key, value) in sources {
                sourceRequests[key] = parseInt(value)
            }
        }
        var scenarioRequests: [String: Int] = [:]
        if let scenarios = json["scenario_requests"] as? [String: Any] {
            for (key, value) in scenarios {
                scenarioRequests[key] = parseInt(value)
            }
        }
        return AgentStatusPayload(
            agentId: json["agent_id"] as? String ?? "",
            hostname: json["hostname"] as? String ?? "",
            role: json["role"] as? String ?? "peer",
            listenURL: json["listen_url"] as? String ?? "",
            routingStrategy: json["routing_strategy"] as? String ?? "",
            draining: json["draining"] as? Bool ?? false,
            reachable: json["reachable"] as? Bool ?? true,
            sourceRequests: sourceRequests,
            scenarioRequests: scenarioRequests,
            capacityRejections: parseInt(json["capacity_rejections"]),
            shardlessFallbacks: parseInt(json["shardless_fallbacks"]),
            peerWarnings: json["peer_warnings"] as? [String] ?? [],
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

    /// `before` is a 1-based line cursor: the window ends at `before - 1`, so
    /// paging backwards is not clobbered by the tab's own refresh. Pass the
    /// previous payload's `nextBefore`; nil means "the newest window".
    static func logs(
        baseURL: URL,
        tail: Int = 200,
        before: Int? = nil
    ) async -> AgentLogsPayload? {
        var path = "/netllm/v1/logs?tail=\(tail)"
        if let before { path += "&before=\(before)" }
        guard let json = await fetchJSON(baseURL: baseURL, path: path) else {
            return nil
        }
        let lines = json["tail"] as? [String] ?? []
        return AgentLogsPayload(
            logDir: json["log_dir"] as? String ?? "",
            logFile: json["log_file"] as? String ?? "",
            exists: json["exists"] as? Bool ?? false,
            sizeBytes: parseInt(json["size_bytes"]),
            tail: lines,
            truncated: json["truncated"] as? Bool ?? false,
            records: (json["records"] as? [[String: Any]] ?? []).map(parseLogRecord),
            totalLines: parseInt(json["total_lines"]),
            nextBefore: parseOptionalInt(json["next_before"]),
            downloadURL: json["download_url"] as? String
        )
    }

    private static func parseLogRecord(_ dict: [String: Any]) -> AgentLogRecord {
        AgentLogRecord(
            lineNo: parseInt(dict["line_no"]),
            ts: dict["ts"] as? String,
            level: dict["level"] as? String,
            levelLabel: dict["level_label"] as? String,
            logger: dict["logger"] as? String,
            message: dict["message"] as? String ?? "",
            raw: dict["raw"] as? String ?? ""
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
                keychainAccount: KeychainStore.accountForCloudProvider(id),
                apiKeyEnv: row["api_key_env"] as? String ?? ""
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

    /// Every provider's stored credential-verification state, read from the
    /// config summary (`cloud.providers.<id>.verification`).
    ///
    /// Read over HTTP rather than out of `document`, even though the app's
    /// config comes from `netllm config export`, because the export is the
    /// raw model dump — it carries the four `verified_*` fields but not the
    /// server-composed `blocker` sentence or the gate's `can_enable` verdict.
    /// Fetching the projection is what keeps the macOS app from having to
    /// write its own copy of either.
    ///
    /// `nil` when the agent is unreachable or predates verification; the
    /// Cloud tab then shows no verdict and blocks nothing.
    static func cloudVerifications(baseURL: URL) async -> [String: CloudVerification]? {
        guard let json = await fetchJSON(baseURL: baseURL, path: "/netllm/v1/config")
        else { return nil }
        let cloud = json["cloud"] as? [String: Any] ?? [:]
        let providers = cloud["providers"] as? [String: Any] ?? [:]
        var out: [String: CloudVerification] = [:]
        for (id, raw) in providers {
            guard let row = raw as? [String: Any],
                  let verification = row["verification"] as? [String: Any]
            else { continue }
            out[id] = CloudVerification.from(verification)
        }
        return out.isEmpty ? nil : out
    }

    /// Checks one provider's credential against the provider and records the
    /// outcome in the agent's config (POST
    /// /netllm/v1/cloud/providers/{id}/verify).
    ///
    /// `apiKey` is a key the user has typed but not yet stored — the agent
    /// checks it and keeps only a fingerprint, so a key never has to be saved
    /// to find out whether it works. On macOS keys live in the login Keychain
    /// and are injected into the agent process at launch, which means a key
    /// saved *since* the agent started is invisible to it; passing the draft
    /// here is what makes Verify answer about the key the user is looking at
    /// rather than the one the agent happens to be holding.
    static func verifyCloudProvider(
        baseURL: URL, providerID: String, apiKey: String?
    ) async -> CloudVerification? {
        var body: [String: Any] = [:]
        if let apiKey, !apiKey.isEmpty { body["api_key"] = apiKey }
        guard let json = await postJSON(
            baseURL: baseURL,
            path: "/netllm/v1/cloud/providers/\(providerID)/verify",
            body: body,
            timeout: 20
        ) else { return nil }
        return CloudVerification.from(json)
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

    static func setDrain(baseURL: URL, draining: Bool) async -> Bool {
        guard let json = await postJSON(
            baseURL: baseURL,
            path: "/netllm/v1/admin/drain",
            body: ["draining": draining]
        ) else { return false }
        return json["ok"] as? Bool ?? false
    }

    static func telemetry(baseURL: URL) async -> TelemetrySnapshot? {
        guard let json = await fetchJSON(
            baseURL: baseURL,
            path: "/netllm/v1/telemetry?watch=1&history=60",
            timeout: 5
        ) else { return nil }
        return TelemetrySnapshot(raw: json)
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

    /// Keeps a JSON `null` distinct from 0 — `next_before` is null at the
    /// start of the file, and line 0 does not exist.
    private static func parseOptionalInt(_ value: Any?) -> Int? {
        if value == nil || value is NSNull { return nil }
        if let value = value as? Int { return value }
        if let value = value as? Double { return Int(value) }
        if let value = value as? NSNumber { return value.intValue }
        return nil
    }

    private static func parsePeer(_ dict: [String: Any]) -> PeerStatus {
        PeerStatus(
            agentId: dict["agent_id"] as? String ?? "",
            listenURL: dict["listen_url"] as? String ?? "",
            role: dict["role"] as? String ?? "peer",
            hostname: dict["hostname"] as? String ?? "",
            discoveredVia: dict["discovered_via"] as? String ?? "",
            alsoReachableAt: dict["also_reachable_at"] as? [String] ?? [],
            addressKinds: parseAddressKinds(dict["reachable_at"])
        )
    }

    /// `reachable_at` -> url: kind (UI-4a). Absent on an agent that predates
    /// the key, which reads as "unclassified" — never as "no alternates".
    static func parseAddressKinds(_ value: Any?) -> [String: String] {
        guard let rows = value as? [[String: Any]] else { return [:] }
        var out: [String: String] = [:]
        for row in rows {
            guard let url = row["url"] as? String, !url.isEmpty else { continue }
            out[url] = row["kind"] as? String ?? ""
        }
        return out
    }

    /// POST twin of `fetchJSON`. Same contract: a non-200 or an unparseable
    /// body is `nil`, never a partially-filled result — a caller cannot tell
    /// a refusal from a success by accident.
    private static func postJSON(
        baseURL: URL, path: String, body: [String: Any], timeout: TimeInterval = 5
    ) async -> [String: Any]? {
        guard let url = AgentHTTP.url(base: baseURL, path: path) else { return nil }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = timeout
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try? JSONSerialization.data(withJSONObject: body)
        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard (response as? HTTPURLResponse)?.statusCode == 200 else { return nil }
            return try JSONSerialization.jsonObject(with: data) as? [String: Any]
        } catch {
            return nil
        }
    }

    private static func fetchJSON(
        baseURL: URL, path: String, timeout: TimeInterval = 5
    ) async -> [String: Any]? {
        guard let url = AgentHTTP.url(base: baseURL, path: path) else { return nil }
        var request = URLRequest(url: url)
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
