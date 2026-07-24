import AppKit
import Foundation
import SwiftUI

@MainActor
@Observable
final class SettingsViewModel {
    var document = NetllmConfigDocument()
    /// Form shape for schema-driven sections (`ui` — see
    /// docs/config-schema-rewrite-plan.md §5 phase 4). nil until the
    /// first successful `reloadAll()`; SchemaFormView call sites fall
    /// back to a "schema unavailable" message when nil.
    var configSchema: ConfigSchema?
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
    /// Live known-harness registry from GET /netllm/v1/harnesses (see
    /// AgentAPI.harnesses) — unlike cloudProviderRegistry this is
    /// refetched every refreshLiveData() cycle, not once per session:
    /// detection reflects whether a CLI is on PATH right now, which can
    /// change mid-session as the user installs one.
    var harnessRegistry: [HarnessInfo] = []
    /// Rasterized harness icons, keyed by harness id, fetched once and
    /// cached for the process lifetime (AgentAPI.harnessIcon) — the icon
    /// set is effectively static, unlike detection state, so this is
    /// loaded lazily on first render rather than every refreshLiveData().
    var harnessIcons: [String: NSImage] = [:]
    private var harnessIconFetchesInFlight: Set<String> = []
    /// UI intent for secured swarm; synced from config on reload, applied on save.
    var requireClusterToken = false
    /// Models tab filter/collapse state (docs/models-ux-plan.md B2).
    /// Lives here, not in @State: the Settings detail view is keyed by
    /// `.id(uiRevision)`, so view-local state would reset on every
    /// 2-second live poll.
    var modelsSearchText = ""
    var modelsCollapsedGroups: Set<String> = []
    /// Cloud tab per-provider drafts, keyed by provider id. Same
    /// `.id(uiRevision)` constraint as above — these were @State in
    /// CloudProviderCard, which the 2s live poll wiped mid-typing (the
    /// "API key disappears" bug). Keychain is read once per provider per
    /// session (nil draft = not loaded yet), also avoiding a Keychain
    /// prompt per poll under ad-hoc signing.
    var cloudKeyDrafts: [String: String] = [:]
    var cloudKeyFeedback: [String: String] = [:]
    /// Fetched provider catalogs (AgentAPI.cloudProviderModels) and the
    /// in-flight marker for the fetch button.
    var cloudCatalogs: [String: CloudModelCatalog] = [:]
    var cloudCatalogFetching: Set<String> = []
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

    /// Candidate refs for a model pool's `hosts` list (docs/models-ux-plan.md
    /// phase A) — deduped union of local backend base_urls and peer agent
    /// ids, the two ref forms a user realistically picks (backend id and
    /// "peer:" prefix stay type-in-able). Peers merge `status.peers` +
    /// `lanPeers` so hosts seen only by subnet scan still appear.
    var knownHostRefs: [SchemaSuggestion] {
        var seen = Set<String>()
        var refs: [SchemaSuggestion] = []
        for backend in status?.backends.filter(\.local) ?? [] {
            let url = backend.baseURL
            guard !url.isEmpty, seen.insert(url).inserted else { continue }
            refs.append(SchemaSuggestion(url, label: "\(backend.provider) · \(url)"))
        }
        let peers = (status?.peers ?? []) + lanPeers
        for peer in peers {
            guard !peer.agentId.isEmpty, seen.insert(peer.agentId).inserted else { continue }
            let label = peer.hostname.isEmpty
                ? peer.agentId
                : "\(peer.hostname) (\(peer.agentId))"
            refs.append(SchemaSuggestion(peer.agentId, label: label))
        }
        return refs
    }

    /// Candidate model IDs for a pool's `models` list — union of every
    /// backend's served models, deduped, sorted case-insensitively.
    var knownModelIDs: [SchemaSuggestion] {
        var seen = Set<String>()
        var ids: [String] = []
        for backend in status?.backends ?? [] {
            for model in backend.models where seen.insert(model).inserted {
                ids.append(model)
            }
        }
        return ids
            .sorted { $0.localizedCaseInsensitiveCompare($1) == .orderedAscending }
            .map { SchemaSuggestion($0) }
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
            configSchema = try? configStore.loadSchema()
            syncRequireClusterTokenFromDocument()
            MenubarAppModel.shared.updateUiSettings(document.ui)
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
            if let harnesses = await AgentAPI.harnesses(baseURL: agentBaseURL) {
                harnessRegistry = harnesses
            }
        } else {
            status = nil
            agentVersion = nil
            routedModels = []
        }
        bumpUI()
    }

    /// Kicks off a fetch for `harness.iconPath` if not already cached or
    /// in flight; safe to call from every render pass (SwiftUI `.task` /
    /// `.onAppear`) since it no-ops once loaded. Callers read
    /// `harnessIcons[harness.id]` and fall back to a generic glyph while
    /// nil.
    func loadHarnessIconIfNeeded(_ harness: HarnessInfo) {
        guard let path = harness.iconPath,
              harnessIcons[harness.id] == nil,
              !harnessIconFetchesInFlight.contains(harness.id)
        else { return }
        harnessIconFetchesInFlight.insert(harness.id)
        let baseURL = agentBaseURL
        Task { @MainActor in
            defer { harnessIconFetchesInFlight.remove(harness.id) }
            if let image = await AgentAPI.harnessIcon(baseURL: baseURL, path: path) {
                harnessIcons[harness.id] = image
                bumpUI()
            }
        }
    }

    private var swarmDiscoveryEnabled: Bool {
        document.swarm.bool("mdns")
            || document.swarm.bool("subnet_scan")
            || !document.swarm.stringArray("peers").isEmpty
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
        requireClusterToken = !document.swarm.string("cluster_token").isEmpty
    }

    func joinCommandText() -> String? {
        let token = document.swarm.string("cluster_token").trimmingCharacters(in: .whitespaces)
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
                MenubarAppModel.shared.updateUiSettings(document.ui)
                needsRestart = true
                setSuccess("Saved config.toml — use Restart Agent for listen/routing changes.")
            }
        }
    }

    private func applyRequireClusterTokenOnSave() {
        if requireClusterToken {
            if document.swarm.string("cluster_token").isEmpty {
                document.swarm["cluster_token"] = .string(ClusterTokenGenerator.make())
            }
        } else {
            document.swarm["cluster_token"] = .string("")
        }
    }

    func providerURLBinding(_ provider: String) -> Binding<[String]> {
        Binding(
            get: { self.document.discovery["provider_urls"]?.objectValue?[provider]?.arrayValue?.compactMap(\.stringValue) ?? [] },
            set: { newValue in
                var providerURLs = self.document.discovery["provider_urls"]?.objectValue ?? [:]
                if newValue.isEmpty {
                    providerURLs.removeValue(forKey: provider)
                } else {
                    providerURLs[provider] = .strings(newValue)
                }
                self.document.discovery["provider_urls"] = .object(providerURLs)
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
        var providers = document.discovery.stringArray("providers")
        if enabled {
            if !providers.contains(id) { providers.append(id) }
        } else {
            providers.removeAll { $0 == id }
        }
        document.discovery["providers"] = .strings(providers)
        bumpUI()
    }

    func providerEnabled(_ id: String) -> Bool {
        document.discovery.stringArray("providers").contains(id)
    }

    @discardableResult
    func addModelAlias() -> String {
        var aliases = document.routing.model_aliases
        var name = "alias"
        var suffix = 1
        while aliases[name] != nil {
            suffix += 1
            name = "alias-\(suffix)"
        }
        aliases[name] = .strings([])
        document.routing.model_aliases = aliases
        bumpUI()
        return name
    }

    @discardableResult
    func addModelPool() -> String {
        var pools = document.routing.model_pools
        var name = "pool"
        var suffix = 1
        while pools[name] != nil {
            suffix += 1
            name = "pool-\(suffix)"
        }
        pools[name] = .object(["enabled": .bool(true), "hosts": .strings([]), "models": .strings([])])
        document.routing.model_pools = pools
        bumpUI()
        return name
    }

    // MARK: - Model pools (Models tab inline editing — docs/models-ux-plan.md B3)
    // All mutations write document.routing.model_pools — the same draft
    // dict the Routing tab's editor binds to, so there is no second
    // source of truth to sync; saving still goes through toolbar Save.

    struct ModelPoolSummary: Identifiable {
        var name: String
        var enabled: Bool
        var hosts: [String]
        var models: [String]
        var id: String { name }
    }

    var modelPoolSummaries: [ModelPoolSummary] {
        document.routing.model_pools.keys.sorted().compactMap { name in
            guard let entry = document.routing.model_pools[name]?.objectValue else { return nil }
            return ModelPoolSummary(
                name: name,
                enabled: entry["enabled"]?.boolValue ?? true,
                hosts: entry["hosts"]?.arrayValue?.compactMap(\.stringValue) ?? [],
                models: entry["models"]?.arrayValue?.compactMap(\.stringValue) ?? []
            )
        }
    }

    func pools(containing model: String) -> [ModelPoolSummary] {
        modelPoolSummaries.filter { $0.models.contains(model) }
    }

    func pools(notContaining model: String) -> [ModelPoolSummary] {
        modelPoolSummaries.filter { !$0.models.contains(model) }
    }

    func addModel(_ model: String, toPool name: String) {
        guard var entry = document.routing.model_pools[name]?.objectValue else { return }
        var models = entry["models"]?.arrayValue?.compactMap(\.stringValue) ?? []
        guard !models.contains(model) else { return }
        models.append(model)
        entry["models"] = .strings(models)
        document.routing.model_pools[name] = .object(entry)
        setSuccess("Added \(model) to pool \(name) — Save to persist.")
    }

    func removeModel(_ model: String, fromPool name: String) {
        guard var entry = document.routing.model_pools[name]?.objectValue else { return }
        var models = entry["models"]?.arrayValue?.compactMap(\.stringValue) ?? []
        models.removeAll { $0 == model }
        entry["models"] = .strings(models)
        document.routing.model_pools[name] = .object(entry)
        setSuccess("Removed \(model) from pool \(name) — Save to persist.")
    }

    /// "New pool…" from a model row: create via the same `pool`/`pool-2`
    /// naming as the Routing tab's Add button, seed it with the model.
    /// Naming/host setup continues on the Routing tab — no modal here.
    func addModelToNewPool(_ model: String) {
        let name = addModelPool()
        addModel(model, toPool: name)
        setSuccess("Created pool \(name) with \(model) — set its hosts on the Routing tab, then Save.")
    }

    /// Client-side pool effectiveness (docs/models-ux-plan.md B3): a pool
    /// is "active" iff ≥1 of its host refs resolves to an online backend
    /// that serves ≥1 pool model — all derivable from /netllm/v1/status.
    /// Returns nil reason when active; a human-readable reason otherwise.
    func poolInactiveReason(_ pool: ModelPoolSummary) -> String? {
        guard pool.enabled else { return "pool disabled" }
        guard let backends = status?.backends else { return "agent not running" }
        if pool.hosts.isEmpty { return "no hosts configured" }
        if pool.models.isEmpty { return "no models configured" }
        let matched = backends.filter { backend in
            pool.hosts.contains { Self.backendMatchesHostRef(backend, ref: $0) }
        }
        let matchedOnline = matched.filter { $0.health == "online" }
        if matchedOnline.isEmpty { return "host offline" }
        let servesPoolModel = matchedOnline.contains { backend in
            backend.models.contains { pool.models.contains($0) }
        }
        return servesPoolModel ? nil : "no pool model served"
    }

    /// Swift mirror of BackendPool._backend_matches_host_ref (pool.py):
    /// ref forms are backend id, "peer:<agent-id>", bare agent_id, or
    /// base_url — keep in sync with the Python side.
    static func backendMatchesHostRef(_ backend: BackendStatus, ref: String) -> Bool {
        let target = ref.trimmingCharacters(in: .whitespaces)
        guard !target.isEmpty else { return false }
        if !backend.backendId.isEmpty {
            if backend.backendId == target { return true }
            if backend.backendId == "peer:\(target)" { return true }
        }
        if !backend.agentId.isEmpty, backend.agentId == target { return true }
        func trimSlash(_ s: String) -> String {
            s.hasSuffix("/") ? String(s.dropLast()) : s
        }
        return trimSlash(backend.baseURL) == trimSlash(target)
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

    func removeBackendOverride(at index: Int) {
        guard document.routing.backends.indices.contains(index) else { return }
        document.routing.backends.remove(at: index)
        bumpUI()
    }

    // MARK: - Cloud providers (key drafts + model allowlist)

    /// Lazy one-shot Keychain read: the draft dictionary is the source
    /// of truth for the text field once populated, so live-poll view
    /// rebuilds never reset what the user typed.
    func loadCloudKeyDraftIfNeeded(_ provider: CloudProviderInfo) {
        guard cloudKeyDrafts[provider.id] == nil else { return }
        cloudKeyDrafts[provider.id] = KeychainStore.load(account: provider.keychainAccount) ?? ""
    }

    func saveCloudKey(_ provider: CloudProviderInfo) {
        let trimmed = (cloudKeyDrafts[provider.id] ?? "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty {
            KeychainStore.delete(account: provider.keychainAccount)
            cloudKeyFeedback[provider.id] = "Cleared."
            bumpUI()
            return
        }
        if trimmed == "netllm-local" {
            cloudKeyFeedback[provider.id] =
                "Use a real API key — netllm-local is the local-mesh placeholder."
            bumpUI()
            return
        }
        do {
            try KeychainStore.save(account: provider.keychainAccount, value: trimmed)
            cloudKeyFeedback[provider.id] = "Saved. Restart the agent to apply."
        } catch {
            cloudKeyFeedback[provider.id] = "Could not save key to Keychain."
        }
        bumpUI()
    }

    func clearCloudKey(_ provider: CloudProviderInfo) {
        KeychainStore.delete(account: provider.keychainAccount)
        cloudKeyDrafts[provider.id] = ""
        cloudKeyFeedback[provider.id] = "Cleared. Restart the agent to drop the injected credential."
        bumpUI()
    }

    func fetchCloudCatalog(_ providerID: String) {
        guard agentReachable, !cloudCatalogFetching.contains(providerID) else { return }
        cloudCatalogFetching.insert(providerID)
        bumpUI()
        Task { [weak self] in
            guard let self else { return }
            let catalog = await AgentAPI.cloudProviderModels(
                baseURL: agentBaseURL, providerID: providerID
            )
            cloudCatalogFetching.remove(providerID)
            if let catalog {
                cloudCatalogs[providerID] = catalog
            } else {
                cloudCatalogFeedbackUnavailable(providerID)
            }
            bumpUI()
        }
    }

    private func cloudCatalogFeedbackUnavailable(_ providerID: String) {
        cloudKeyFeedback[providerID] =
            "Could not fetch the model catalog — is the agent running (and restarted after key changes)?"
    }

    /// Allowlist semantics mirror the server: empty = every model the
    /// provider serves. First uncheck materializes the explicit list.
    func cloudModelEnabled(_ providerID: String, model: String) -> Bool {
        let allowlist = document.cloud.providers[providerID]?.models ?? []
        return allowlist.isEmpty || allowlist.contains(model)
    }

    func toggleCloudModel(_ providerID: String, model: String, enabled: Bool) {
        var config = document.cloud.providers[providerID] ?? .init()
        if config.models.isEmpty {
            guard !enabled else { return }
            // Materialize "all" as the fetched catalog minus the one
            // being disabled — needs a catalog to know what "all" is.
            guard let catalog = cloudCatalogs[providerID] else { return }
            config.models = catalog.models.filter { $0 != model }
        } else if enabled {
            if !config.models.contains(model) { config.models.append(model) }
        } else {
            config.models.removeAll { $0 == model }
        }
        document.cloud.providers[providerID] = config
        bumpUI()
    }

    /// Back to "all models" (empty allowlist — the server default).
    func resetCloudModels(_ providerID: String) {
        guard var config = document.cloud.providers[providerID] else { return }
        config.models = []
        document.cloud.providers[providerID] = config
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
