import Foundation
import SwiftUI

@MainActor
@Observable
final class SettingsViewModel {
    var document = NetllmConfigDocument()
    var status: AgentStatusPayload?
    var agentVersion: AgentVersionPayload?
    var discoverProviders: [DiscoverProvider] = []
    var lanPeers: [PeerStatus] = []
    var routedModels: [ModelRow] = []
    var localModels: [ModelRow] = []
    var doctorIssues: [DoctorIssue] = []
    var doctorOK = true
    var agentReachable = false
    var isLoading = false
    var activeAction: String?
    var message: String?
    var errorMessage: String?
    var needsRestart = false
    var agentLogs: AgentLogsPayload?
    /// Live cloud provider registry from GET /netllm/v1/cloud/providers
    /// (single source of truth — see AgentAPI.cloudProviderRegistry). Empty
    /// until the first successful fetch, or when the agent is unreachable;
    /// `cloudProviders` below falls back to Self.cloudProvidersBootstrap.
    var cloudProviderRegistry: [CloudProviderInfo] = []
    /// UI intent for secured swarm; synced from config on reload, applied on save.
    var requireClusterToken = false
    private(set) var uiRevision = 0

    private var livePollTask: Task<Void, Never>?
    private var autoPeerScanTask: Task<Void, Never>?
    private var didAutoPeerScan = false

    let configStore: ConfigStore
    let cli: CLIRunner
    private(set) var agentBaseURL: URL

    static let strategies = [
        "auto", "local_first", "local_spillover", "failover", "round_robin",
        "least_load", "latency_weighted", "batch_shard",
    ]
    static let providers = ["omlx", "ollama", "lmstudio", "vllm"]
    static let roles = ["peer", "gateway"]

    // Offline-only fallback (agent unreachable / GET /netllm/v1/cloud/providers
    // failed) — mirrors netllm_core.cloud_providers.CLOUD_PROVIDERS as it
    // stood when this file was last touched. `cloudProviders` below always
    // prefers the live `cloudProviderRegistry` when populated, so this list
    // drifting from the Python registry only affects the brief window before
    // the first successful fetch, not steady-state display.
    static let cloudProvidersBootstrap: [CloudProviderInfo] = [
        CloudProviderInfo(
            id: "moonshot",
            displayName: "Moonshot AI (Kimi)",
            notes: "Pay-as-you-go API keys only; no OAuth/plan auth.",
            regions: ["global", "cn"],
            keychainAccount: KeychainStore.accountForCloudProvider("moonshot")
        ),
        CloudProviderInfo(
            id: "zai",
            displayName: "Z.ai (Zhipu GLM)",
            notes: "GLM Coding Plan keys are restricted to an approved-tools list "
                + "per Z.ai's usage policy.",
            regions: ["api", "coding_plan", "cn"],
            keychainAccount: KeychainStore.accountForCloudProvider("zai")
        ),
        CloudProviderInfo(
            id: "openai",
            displayName: "OpenAI",
            notes: "API key only — no public OAuth client for third-party tools.",
            regions: ["global"],
            keychainAccount: KeychainStore.accountForCloudProvider("openai")
        ),
        CloudProviderInfo(
            id: "anthropic",
            displayName: "Anthropic",
            notes: "Console API key (x-api-key).",
            regions: ["global"],
            keychainAccount: KeychainStore.accountForCloudProvider("anthropic")
        ),
        CloudProviderInfo(
            id: "openrouter",
            displayName: "OpenRouter",
            notes: "Also supports OAuth PKCE sign-in for a user-scoped key.",
            regions: ["global"],
            keychainAccount: KeychainStore.accountForCloudProvider("openrouter")
        ),
    ]

    /// The provider list to render: live registry when available, offline
    /// bootstrap otherwise. Always use this, never the static list directly.
    var cloudProviders: [CloudProviderInfo] {
        cloudProviderRegistry.isEmpty ? Self.cloudProvidersBootstrap : cloudProviderRegistry
    }

    /// Peers the running agent is routing through (`/netllm/v1/status`).
    var connectedPeerCount: Int { status?.peers.count ?? 0 }

    /// Unique agents from the last `peers --subnet-scan` (may not be connected yet).
    var discoveredLanPeerCount: Int {
        Set(lanPeers.map(\.listenURL)).count
    }

    var peerStatValue: String {
        let connected = connectedPeerCount
        let discovered = discoveredLanPeerCount
        if discovered > 0, discovered != connected {
            return "\(connected)/\(discovered)"
        }
        return "\(connected)"
    }

    var routedModelCount: Int {
        if !routedModels.isEmpty { return routedModels.count }
        let fromStatus = aggregatedModelCountFromStatus
        if fromStatus > 0 { return fromStatus }
        return Set(localModels.map(\.model)).count
    }

    var routedModelStatSubtitle: String {
        if !routedModels.isEmpty { return "Routed catalog" }
        if aggregatedModelCountFromStatus > 0 {
            return "From backend health"
        }
        if !localModels.isEmpty { return "From provider discover scan" }
        return "Run Discover or start oMLX/Ollama"
    }

    private var aggregatedModelCountFromStatus: Int {
        guard let status else { return 0 }
        var seen = Set<String>()
        var fallbackCount = 0
        for backend in status.backends where backend.health == "online" {
            if backend.models.isEmpty {
                fallbackCount += backend.modelCount
            } else {
                for model in backend.models {
                    seen.insert(model)
                }
            }
        }
        return max(seen.count, fallbackCount)
    }

    var peerStatSubtitle: String {
        let connected = connectedPeerCount
        let discovered = discoveredLanPeerCount
        if discovered > connected {
            return "Connected / found on LAN"
        }
        if connected > 0 {
            return "Connected swarm agents"
        }
        if discovered > 0 {
            return "Found on LAN"
        }
        return "LAN swarm agents"
    }

    init(runtime: PythonRuntime, configPath: URL = AppConfig.defaultConfigPath()) {
        configStore = ConfigStore(runtime: runtime, configPath: configPath)
        cli = CLIRunner(runtime: runtime, configPath: configPath)
        agentBaseURL = URL(string: "http://127.0.0.1:11400")!
    }

    func reloadAll() async {
        await runAction("Reloading…") {
            didAutoPeerScan = false
            document = try configStore.load()
            syncRequireClusterTokenFromDocument()
            updateAgentURL()
            await refreshLiveData()
            scheduleAutoPeerScanIfNeeded()
            setSuccess("Config and live status refreshed.")
        }
    }

    /// Poll agent health while Settings is open so stats update without quit/restart.
    func startLivePolling() {
        livePollTask?.cancel()
        livePollTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                await self.refreshLiveData()
                try? await Task.sleep(for: .seconds(2))
            }
        }
    }

    func stopLivePolling() {
        livePollTask?.cancel()
        livePollTask = nil
        autoPeerScanTask?.cancel()
        autoPeerScanTask = nil
    }

    /// After Restart Agent, wait until /health responds before refreshing stats.
    func waitForAgentHealth(maxAttempts: Int = 30) async {
        updateAgentURL()
        for _ in 0..<maxAttempts {
            if Task.isCancelled { return }
            if await AgentAPI.isReachable(baseURL: agentBaseURL) {
                agentReachable = true
                bumpUI()
                return
            }
            try? await Task.sleep(for: .seconds(1))
        }
    }

    func fetchLogs() async {
        guard agentReachable else {
            agentLogs = nil
            bumpUI()
            return
        }
        agentLogs = await AgentAPI.logs(baseURL: agentBaseURL, tail: 200)
        bumpUI()
    }

    func refreshLiveData() async {
        updateAgentURL()
        let wasReachable = agentReachable
        agentReachable = await AgentAPI.isReachable(baseURL: agentBaseURL)
        if agentReachable {
            async let statusTask = AgentAPI.status(baseURL: agentBaseURL)
            async let versionTask = AgentAPI.version(baseURL: agentBaseURL)
            async let modelsTask = AgentAPI.models(baseURL: agentBaseURL)
            status = await statusTask
            agentVersion = await versionTask
            routedModels = await modelsTask
            if routedModels.isEmpty, let status {
                routedModels = AgentAPI.modelsFromStatus(status)
            }
            syncDiscoverProvidersFromStatus()
            if !wasReachable {
                scheduleAutoPeerScanIfNeeded()
            }
            // Static registry data — fetch once per session, not every poll.
            if cloudProviderRegistry.isEmpty {
                if let registry = await AgentAPI.cloudProviderRegistry(baseURL: agentBaseURL) {
                    cloudProviderRegistry = registry
                }
            }
        } else {
            status = nil
            agentVersion = nil
            routedModels = []
        }
        bumpUI()
    }

    private var swarmDiscoveryEnabled: Bool {
        document.swarm.mdns
            || document.swarm.subnet_scan
            || !document.swarm.peers.isEmpty
            || document.bindHost == "0.0.0.0"
    }

    private func scheduleAutoPeerScanIfNeeded() {
        guard agentReachable, swarmDiscoveryEnabled, !didAutoPeerScan else { return }
        autoPeerScanTask?.cancel()
        autoPeerScanTask = Task { [weak self] in
            await self?.autoDiscoverLanPeers()
        }
    }

    /// Background subnet scan for Settings stats (no save; agent merges peers at runtime).
    private func autoDiscoverLanPeers() async {
        guard !didAutoPeerScan else { return }
        didAutoPeerScan = true
        if let result = await AgentAPI.peersScan(baseURL: agentBaseURL) {
            lanPeers = result.peers
            bumpUI()
        }
    }

    /// Agent discovers providers on startup; mirror that in the Settings UI without a manual scan.
    private func syncDiscoverProvidersFromStatus() {
        guard let status else { return }
        let locals = status.backends.filter(\.local)
        guard !locals.isEmpty else { return }
        discoverProviders = locals.map { backend in
            DiscoverProvider(
                id: backend.provider,
                name: backend.provider,
                baseURL: backend.baseURL,
                status: backend.health,
                models: backend.models
            )
        }
        localModels = locals.flatMap { backend in
            backend.models.map { model in
                ModelRow(
                    id: "\(backend.provider)-\(model)",
                    model: model,
                    provider: backend.provider,
                    host: backend.baseURL,
                    scope: "local"
                )
            }
        }
    }

    func syncRequireClusterTokenFromDocument() {
        requireClusterToken = !document.swarm.cluster_token.isEmpty
    }

    func joinCommandText() -> String? {
        let token = document.swarm.cluster_token.trimmingCharacters(in: .whitespaces)
        guard !token.isEmpty else { return nil }
        let listenURL = status?.listenURL.trimmingCharacters(in: .whitespaces) ?? ""
        guard !listenURL.isEmpty else { return nil }
        return JoinCommandExporter.format(listenURL: listenURL, token: token)
    }

    func copyJoinCommand() {
        guard let command = joinCommandText() else { return }
        JoinCommandExporter.copyToPasteboard(command)
        setSuccess("Join command copied to clipboard.")
    }

    func save() {
        Task {
            await runAction("Saving config…") {
                document.applyLanMeshDefaults()
                applyRequireClusterTokenOnSave()
                _ = try configStore.save(document)
                syncRequireClusterTokenFromDocument()
                needsRestart = true
                setSuccess("Saved config.toml — use Restart Agent for listen/routing changes.")
            }
        }
    }

    private func applyRequireClusterTokenOnSave() {
        if requireClusterToken {
            if document.swarm.cluster_token.isEmpty {
                document.swarm.cluster_token = ClusterTokenGenerator.make()
            }
        } else {
            document.swarm.cluster_token = ""
        }
    }

    func providerURLBinding(_ provider: String) -> Binding<[String]> {
        Binding(
            get: { self.document.discovery.provider_urls[provider] ?? [] },
            set: { newValue in
                if newValue.isEmpty {
                    self.document.discovery.provider_urls.removeValue(forKey: provider)
                } else {
                    self.document.discovery.provider_urls[provider] = newValue
                }
                self.bumpUI()
            }
        )
    }

    func runDiscover(saveURLs: Bool = true) {
        Task {
            await runAction("Discovering local providers…") {
                var command = ["discover", "--json"]
                if saveURLs { command.append("--save-urls") }
                let json = try parseCLIJSON(command: command)
                guard let providers = json["providers"] as? [[String: Any]] else {
                    throw ActionError.unexpectedResponse("discover")
                }
                if saveURLs {
                    document = try configStore.load()
                }
                discoverProviders = providers.map { row in
                    DiscoverProvider(
                        id: row["id"] as? String ?? UUID().uuidString,
                        name: row["name"] as? String ?? "",
                        baseURL: row["base_url"] as? String ?? "",
                        status: row["status"] as? String ?? "offline",
                        models: row["models"] as? [String] ?? []
                    )
                }
                localModels = discoverProviders.flatMap { provider in
                    provider.models.map { model in
                        ModelRow(
                            id: "\(provider.id)-\(model)",
                            model: model,
                            provider: provider.id,
                            host: provider.name,
                            scope: "local"
                        )
                    }
                }
                let online = discoverProviders.filter { $0.status == "online" }.count
                setSuccess("Discover complete: \(online)/\(discoverProviders.count) provider(s) online.")
                await refreshLiveData()
            }
        }
    }

    func runPeersScan(save: Bool = false) {
        Task {
            let label = save ? "Scanning LAN and saving peers…" : "Scanning LAN for peers…"
            await runAction(label) {
                try await applyPeersScan(save: save, showManualHints: true)
            }
        }
    }

    private func applyPeersScan(save: Bool, showManualHints: Bool) async throws {
        if agentReachable, let result = await AgentAPI.peersScan(baseURL: agentBaseURL, save: save) {
            lanPeers = result.peers
            let warnings = result.warnings
            if save {
                document = try configStore.load()
                needsRestart = true
            }
            if showManualHints {
                if lanPeers.isEmpty {
                    let hint = warnings.isEmpty
                        ? "mDNS often fails on Wi‑Fi — subnet scan also found none."
                        : warnings
                    setSuccess("Scan complete — no LAN agents found. \(hint)")
                } else {
                    let names = lanPeers.map { $0.hostname.isEmpty ? $0.listenURL : $0.hostname }
                        .joined(separator: ", ")
                    var msg = "Found \(lanPeers.count) LAN agent(s): \(names)."
                    if save {
                        msg += " Restart agent to merge remote backends."
                    } else if connectedPeerCount == 0 {
                        msg += " Peers connect automatically when subnet scan is enabled."
                    }
                    if !warnings.isEmpty { msg += " \(warnings)" }
                    setSuccess(msg)
                }
            }
            return
        }

        var args = ["peers", "--json", "--subnet-scan"]
        if save { args.append("--save") }
        let json = try parseCLIJSON(command: args)
        guard let peers = json["peers"] as? [[String: Any]] else {
            throw ActionError.unexpectedResponse("peers")
        }
        let warnings = (json["warnings"] as? [String] ?? []).joined(separator: " ")
        lanPeers = peers.map { row in
            PeerStatus(
                agentId: row["agent_id"] as? String ?? "",
                listenURL: row["listen_url"] as? String ?? "",
                role: row["role"] as? String ?? "peer",
                hostname: row["hostname"] as? String ?? ""
            )
        }
        if save {
            document = try configStore.load()
            needsRestart = true
        }
        if showManualHints {
            if lanPeers.isEmpty {
                let hint = warnings.isEmpty
                    ? "mDNS often fails on Wi‑Fi — subnet scan also found none."
                    : warnings
                setSuccess("Scan complete — no LAN agents found. \(hint)")
            } else {
                let names = lanPeers.map { $0.hostname.isEmpty ? $0.listenURL : $0.hostname }
                    .joined(separator: ", ")
                var msg = "Found \(lanPeers.count) LAN agent(s): \(names)."
                if save {
                    msg += " Restart agent to merge remote backends."
                } else {
                    msg += " Use Scan & save, then Restart agent."
                }
                if !warnings.isEmpty { msg += " \(warnings)" }
                setSuccess(msg)
            }
        }
    }

    func runDoctor() {
        Task {
            await runAction("Running doctor…") {
                let json = try parseCLIJSON(command: ["doctor", "--json"], allowFailure: true)
                doctorOK = json["ok"] as? Bool ?? false
                doctorIssues = (json["issues"] as? [[String: Any]] ?? []).map {
                    DoctorIssue(
                        title: $0["title"] as? String ?? "",
                        fix: $0["fix"] as? String ?? ""
                    )
                }
                if doctorOK {
                    setSuccess("Doctor: all checks passed.")
                } else {
                    setSuccess("Doctor found \(doctorIssues.count) issue(s).")
                }
            }
        }
    }

    func runGateway() {
        Task {
            await runAction("Enabling gateway role…") {
                _ = try cli.run(["gateway"])
                document = try configStore.load()
                needsRestart = true
                setSuccess("Gateway role saved — restart agent to apply.")
            }
        }
    }

    func runTest() {
        Task {
            await runAction("Running latency test…") {
                _ = try cli.run(["test"])
                setSuccess("Latency test passed.")
            }
        }
    }

    func toggleProvider(_ id: String, enabled: Bool) {
        if enabled {
            if !document.discovery.providers.contains(id) {
                document.discovery.providers.append(id)
            }
        } else {
            document.discovery.providers.removeAll { $0 == id }
        }
        bumpUI()
    }

    func providerEnabled(_ id: String) -> Bool {
        document.discovery.providers.contains(id)
    }

    func addCustomEndpoint() {
        document.discovery.custom_endpoints.append("http://127.0.0.1:8080/v1")
        bumpUI()
    }

    func addBackendOverride() {
        document.routing.backends.append(
            NetllmConfigDocument.BackendOverride(
                base_url: "http://127.0.0.1:8080/v1",
                provider: "omlx",
                enabled: true,
                local: true
            )
        )
        bumpUI()
    }

    func addRoutingPolicy() {
        document.routing.policies.append(
            NetllmConfigDocument.RoutingPolicy(
                name: "local-openai",
                api_format: "openai",
                strategy: "local_first",
                allow_cloud: false,
                enabled: true
            )
        )
        bumpUI()
    }

    func removeRoutingPolicy(at index: Int) {
        guard document.routing.policies.indices.contains(index) else { return }
        document.routing.policies.remove(at: index)
        bumpUI()
    }

    func addPeerURL() {
        document.swarm.peers.append("http://127.0.0.1:11400")
        bumpUI()
    }

    func addSubnetCIDR() {
        document.swarm.subnet_cidrs.append("192.168.1.0/24")
        bumpUI()
    }

    func removeSubnetCIDR(at index: Int) {
        guard document.swarm.subnet_cidrs.indices.contains(index) else { return }
        document.swarm.subnet_cidrs.remove(at: index)
        bumpUI()
    }

    func removePeerURL(at index: Int) {
        guard document.swarm.peers.indices.contains(index) else { return }
        document.swarm.peers.remove(at: index)
        bumpUI()
    }

    func removeCustomEndpoint(at index: Int) {
        guard document.discovery.custom_endpoints.indices.contains(index) else { return }
        document.discovery.custom_endpoints.remove(at: index)
        bumpUI()
    }

    func removeBackendOverride(at index: Int) {
        guard document.routing.backends.indices.contains(index) else { return }
        document.routing.backends.remove(at: index)
        bumpUI()
    }

    // MARK: - Private

    private enum ActionError: LocalizedError {
        case unexpectedResponse(String)

        var errorDescription: String? {
            switch self {
            case .unexpectedResponse(let cmd):
                return "Unexpected response from netllm \(cmd). Try rebuilding the app bundle."
            }
        }
    }

    private func runAction(_ label: String, _ work: () async throws -> Void) async {
        isLoading = true
        activeAction = label
        errorMessage = nil
        defer {
            isLoading = false
            activeAction = nil
            bumpUI()
        }
        do {
            try await work()
        } catch {
            errorMessage = error.localizedDescription
            message = nil
            bumpUI()
        }
    }

    private func setSuccess(_ text: String) {
        message = text
        errorMessage = nil
        bumpUI()
    }

    private func bumpUI() {
        uiRevision += 1
    }

    private func parseCLIJSON(command: [String], allowFailure: Bool = false) throws -> [String: Any] {
        let raw: String
        do {
            raw = try cli.run(command)
        } catch let error as CLIRunner.CLIError {
            if allowFailure, case .failed(_, _, let stdout) = error, !stdout.isEmpty {
                raw = stdout
            } else {
                throw error
            }
        }
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let data = trimmed.data(using: .utf8),
              let json = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw ActionError.unexpectedResponse(command.joined(separator: " "))
        }
        return json
    }

    private func updateAgentURL() {
        let host = AppConfig.connectableHost(for: document.bindHost)
        agentBaseURL = URL(string: "http://\(host):\(document.port)")!
    }
}
