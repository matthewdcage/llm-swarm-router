import AppKit
import SwiftUI

@MainActor
struct SettingsWindowView: View {
    @Bindable var model: SettingsViewModel
    @Bindable var supervisor: AgentSupervisor
    @Bindable var updateController: UpdateController
    var onRestartAgent: (() -> Void)?

    @State private var tab = "status"
    @State private var portText = "11400"

    var body: some View {
        NavigationSplitView {
            List(selection: $tab) {
                Section("Server") {
                    sidebarRow("Status", "gauge.with.dots.needle.67percent", "status", "Server status")
                    sidebarRow("Backends", "server.rack", "backends", "Backends")
                    sidebarRow("Models", "cube.box", "models", "Models")
                    sidebarRow("Peers", "point.3.connected.trianglepath.dotted", "peers", "Swarm peers")
                }
                Section("Config") {
                    sidebarRow("Agent", "antenna.radiowaves.left.and.right", "agent", "Agent settings")
                    sidebarRow("Discovery", "magnifyingglass", "discovery", "Discovery settings")
                    sidebarRow("Swarm", "network", "swarm", "Swarm settings")
                    sidebarRow("Routing", "arrow.triangle.branch", "routing", "Routing settings")
                    sidebarRow("Cloud", "cloud", "cloud", "Cloud provider settings")
                    sidebarRow("UI", "slider.horizontal.3", "ui", "App UI settings")
                }
                Section("Tools") {
                    sidebarRow("Logs", "doc.text", "logs", "Agent logs")
                    sidebarRow("Doctor & Test", "stethoscope", "tools", "Doctor and test tools")
                }
            }
            .accessibilityLabel("Settings sections")
            .navigationSplitViewColumnWidth(min: 180, ideal: 200)
        } detail: {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    tabContent
                    feedbackBanner
                }
                .padding(20)
                .id(model.uiRevision)
            }
            .frame(minWidth: 640, minHeight: 520)
            .toolbar {
                ToolbarItemGroup(placement: .automatic) {
                    Button("Refresh") { Task { await model.reloadAll() } }
                        .disabled(model.isLoading)
                    Button("Save") { model.save() }
                        .disabled(model.isLoading)
                    if model.needsRestart {
                        Button("Restart Agent") { restartAgent() }
                            .disabled(model.isLoading)
                    }
                }
            }
        }
        .task {
            await model.reloadAll()
            model.startLivePolling()
        }
        .onDisappear { model.stopLivePolling() }
        .onAppear { portText = String(model.document.port) }
    }

    private func restartAgent() {
        supervisor.restart()
        onRestartAgent?()
        Task {
            await model.waitForAgentHealth()
            await model.refreshLiveData()
            model.needsRestart = false
        }
    }

    @ViewBuilder
    private var tabContent: some View {
        switch tab {
        case "status", "overview": statusTab
        case "backends": backendsTab
        case "models": modelsTab
        case "peers": peersTab
        case "agent": agentTab
        case "discovery": discoveryTab
        case "swarm": swarmTab
        case "routing": routingTab
        case "cloud": cloudTab
        case "ui": uiTab
        case "logs": logsTab
        case "tools": toolsTab
        default: statusTab
        }
    }

    private func sidebarRow(
        _ title: String,
        _ icon: String,
        _ tag: String,
        _ accessibilityHint: String
    ) -> some View {
        Label(title, systemImage: icon)
            .tag(tag)
            .accessibilityLabel(title)
            .accessibilityHint(accessibilityHint)
    }

    @ViewBuilder
    private var feedbackBanner: some View {
        if model.isLoading || model.errorMessage != nil || model.message != nil {
            VStack(alignment: .leading, spacing: 6) {
                if model.isLoading {
                    HStack(spacing: 8) {
                        ProgressView().controlSize(.small)
                        Text(model.activeAction ?? "Working…")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                if let err = model.errorMessage {
                    Label(err, systemImage: "exclamationmark.triangle.fill")
                        .font(.caption)
                        .foregroundStyle(.red)
                } else if let msg = model.message {
                    Label(msg, systemImage: "checkmark.circle.fill")
                        .font(.caption)
                        .foregroundStyle(.green)
                }
            }
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.quaternary.opacity(0.35))
            .clipShape(RoundedRectangle(cornerRadius: 8))
        }
    }

    private var statusTab: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text("Status")
                .font(.largeTitle.weight(.semibold))

            StatusHeroCard(
                version: AppVersionInfo.short,
                listenURL: model.status?.listenURL ?? model.agentBaseURL.absoluteString,
                supervisorLabel: supervisor.statusLabel,
                isRunning: supervisor.isRunning,
                isReachable: model.agentReachable,
                onRestart: { restartAgent() },
                onStop: {
                    supervisor.stop()
                    Task { await model.refreshLiveData() }
                },
                onStart: {
                    supervisor.start()
                    Task {
                        try? await Task.sleep(for: .seconds(1))
                        await model.refreshLiveData()
                    }
                }
            )

            UpdateBannerCard(controller: updateController)

            HStack {
                SettingsSectionTitle(title: "Routing stats")
                Spacer()
                Button("Refresh") { Task { await model.reloadAll() } }
                    .buttonStyle(.borderless)
                    .font(.caption)
                    .disabled(model.isLoading)
            }

            HStack(spacing: 12) {
                StatMetricCard(
                    title: "Backends",
                    value: backendStatValue,
                    subtitle: backendStatSubtitle
                )
                StatMetricCard(
                    title: "Peers",
                    value: model.peerStatValue,
                    subtitle: model.peerStatSubtitle
                )
                StatMetricCard(
                    title: "Models",
                    value: "\(model.routedModelCount)",
                    subtitle: model.routedModelStatSubtitle
                )
            }

            SettingsSectionTitle(title: "Active now")
            SettingsSurfaceCard {
                if let status = model.status, model.agentReachable {
                    Text(activeSummary(status))
                        .font(.subheadline)
                } else if supervisor.isRunning {
                    Text("Agent process is running — waiting for HTTP health at \(model.agentBaseURL.absoluteString).")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                } else {
                    Text("Agent stopped — start from the card above or the menu bar.")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
            }

            SettingsSectionTitle(title: "System")
            SettingsSurfaceCard {
                VStack(alignment: .leading, spacing: 8) {
                    SettingsInfoRow(label: "Platform", value: AppVersionInfo.platformLine)
                    SettingsInfoRow(label: "App version", value: AppVersionInfo.display)
                    if let agentVersion = model.agentVersion, !agentVersion.version.isEmpty {
                        SettingsInfoRow(label: "Agent version", value: "v\(agentVersion.version)")
                    }
                    if let agentVersion = model.agentVersion, !agentVersion.openaiSDK.isEmpty {
                        SettingsInfoRow(label: "OpenAI SDK", value: "v\(agentVersion.openaiSDK)")
                    }
                    if let agentVersion = model.agentVersion, !agentVersion.anthropicSDK.isEmpty {
                        SettingsInfoRow(label: "Anthropic SDK", value: "v\(agentVersion.anthropicSDK)")
                    }
                    SettingsInfoRow(label: "CLI", value: AppBranding.cliCommand)
                    if let status = model.status {
                        SettingsInfoRow(label: "Agent ID", value: status.agentId)
                        SettingsInfoRow(label: "Hostname", value: status.hostname)
                        SettingsInfoRow(label: "Role", value: status.role)
                        SettingsInfoRow(label: "Strategy", value: status.routingStrategy)
                    }
                    SettingsInfoRow(label: "Config", value: AppConfig.defaultConfigPath().path)
                }
            }

            SettingsSectionTitle(title: "Quick actions")
            actionButtons {
                Button("Refresh provider scan") { model.runDiscover() }
                Button("Scan LAN peers") { model.runPeersScan() }
                Button("Run doctor") { model.runDoctor() }
            }
        }
    }

    private var backendStatValue: String {
        guard let status = model.status else { return "—" }
        let online = status.backends.filter { $0.health == "online" }.count
        return "\(online)/\(status.backends.count)"
    }

    private var backendStatSubtitle: String {
        guard let status = model.status else { return "Start agent to load" }
        let online = status.backends.filter { $0.health == "online" }.count
        return online > 0 ? "Online backends" : "No backends online"
    }

    private func activeSummary(_ status: AgentStatusPayload) -> String {
        let online = status.backends.filter { $0.health == "online" }.count
        var parts = [
            "\(online) backend\(online == 1 ? "" : "s") online",
            "role: \(status.role)",
            status.routingStrategy,
        ]
        let connected = model.connectedPeerCount
        let discovered = model.discoveredLanPeerCount
        if connected > 0 {
            parts.insert("\(connected) peer\(connected == 1 ? "" : "s") connected", at: 0)
        } else if discovered > 0 {
            parts.insert("\(discovered) peer\(discovered == 1 ? "" : "s") on LAN", at: 0)
        }
        return parts.joined(separator: " · ")
    }

    private var backendsTab: some View {
        VStack(alignment: .leading, spacing: 12) {
            sectionHeader("Routed backends (from agent)")
            if let backends = model.status?.backends, !backends.isEmpty {
                ForEach(backends) { backend in
                    backendRow(backend)
                }
            } else {
                Text("No backends yet — start oMLX or Ollama on this Mac. The agent finds them automatically.")
                    .foregroundStyle(.secondary)
            }
            sectionHeader("Local providers")
            actionButtons {
                Button("Refresh scan") { model.runDiscover() }
            }
            if model.discoverProviders.isEmpty && !model.isLoading {
                Text("The agent scans oMLX, Ollama, and LM Studio when it starts. Refresh after starting backends.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            ForEach(model.discoverProviders) { provider in
                HStack {
                    statusDot(provider.status == "online")
                    VStack(alignment: .leading) {
                        Text(provider.name).font(.headline)
                        Text(provider.baseURL).font(.caption).foregroundStyle(.secondary)
                        Text("\(provider.models.count) models · \(provider.status)")
                            .font(.caption2).foregroundStyle(.secondary)
                    }
                }
                .padding(.vertical, 4)
            }
        }
    }

    private var modelsTab: some View {
        VStack(alignment: .leading, spacing: 12) {
            sectionHeader("Routed models (agent)")
            modelList(model.routedModels, empty: "No routed models — start agent and backends.")
            sectionHeader("Local provider models")
            actionButtons {
                Button("Refresh via discover") { model.runDiscover() }
            }
            modelList(model.localModels, empty: "Start oMLX/Ollama — models appear when the agent finds them.")
            sectionHeader("LAN models")
            actionButtons {
                Button("Scan peers") { model.runPeersScan() }
            }
            Text("Full LAN model merge: `netllm models --lan` in terminal.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    private var peersTab: some View {
        VStack(alignment: .leading, spacing: 12) {
            sectionHeader("Connected swarm peers")
            if let peers = model.status?.peers, !peers.isEmpty {
                ForEach(peers) { peer in peerRow(peer) }
            } else {
                Text("No peers connected to running agent.").foregroundStyle(.secondary)
            }
            sectionHeader("LAN discovery")
            Text("Subnet scan runs automatically when the agent is healthy (enable Subnet scan in Swarm). Wi‑Fi often blocks mDNS; static peers in config still work.")
                .font(.caption).foregroundStyle(.secondary)
            actionButtons {
                Button("Scan network") { model.runPeersScan() }
                Button("Scan & save to config") { model.runPeersScan(save: true) }
            }
            if model.lanPeers.isEmpty && !model.isLoading && model.message == nil && model.errorMessage == nil {
                Text("Scanning automatically when agent is ready (~10s on a /24).")
                    .font(.caption).foregroundStyle(.secondary)
            } else if model.lanPeers.isEmpty && model.message != nil {
                Text("Last scan returned no LAN agents.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            ForEach(model.lanPeers) { peer in peerRow(peer) }
            sectionHeader("Static peers in config")
            EditableStringList(
                items: $model.document.swarm.stringArray("peers"),
                placeholder: "http://10.0.0.32:11400",
                defaultNew: "http://127.0.0.1:11400"
            )
            .id("peers-tab-\(model.uiRevision)")
        }
    }

    private var agentTab: some View {
        VStack(alignment: .leading, spacing: 12) {
            sectionHeader("Agent")
            Toggle("LAN mode (0.0.0.0)", isOn: Binding(
                get: { model.document.isLanMode },
                set: { model.document.setLanMode($0, port: model.document.port) }
            ))
            HStack {
                Text("Port")
                TextField("11400", text: $portText)
                    .frame(width: 80)
                    .onChange(of: portText) { _, newValue in
                        if let port = Int(newValue) {
                            model.document.setListen(host: model.document.bindHost, port: port)
                        }
                    }
            }
            Picker("Role", selection: $model.document.agent.role) {
                ForEach(SettingsViewModel.roles, id: \.self) { Text($0).tag($0) }
            }
            Toggle("Advertise on LAN", isOn: $model.document.agent.advertise)
            gridRow("Agent ID", model.document.agent.agent_id)
            gridRow("Hostname", model.document.agent.hostname)
            gridRow("Listen", model.document.agent.listen)
            // The bind address (0.0.0.0) is not what peers dial — show
            // the resolved LAN URL the live agent actually advertises.
            gridRow("LAN address", model.status?.listenURL ?? "agent not running")
            Text("Changes apply after Save + Restart Agent.")
                .font(.caption).foregroundStyle(.orange)
            if model.document.isLanMode && !model.requireClusterToken {
                Label("Open trusted-LAN swarm. Enable Require cluster token on the Swarm tab for untrusted networks.", systemImage: "info.circle")
                    .foregroundStyle(.secondary)
                    .font(.caption)
            }
        }
    }

    private var discoveryTab: some View {
        VStack(alignment: .leading, spacing: 12) {
            sectionHeader("Providers")
            ForEach(SettingsViewModel.providers, id: \.self) { provider in
                Toggle(provider, isOn: Binding(
                    get: { model.providerEnabled(provider) },
                    set: { model.toggleProvider(provider, enabled: $0) }
                ))
            }
            sectionHeader("Provider URLs")
            Text("Leave empty to auto-scan default ports (oMLX: 8080, 8088, 8081).")
                .font(.caption)
                .foregroundStyle(.secondary)
            ForEach(SettingsViewModel.providers, id: \.self) { provider in
                VStack(alignment: .leading, spacing: 4) {
                    Text(provider.capitalized)
                        .font(.caption.weight(.medium))
                    EditableStringList(
                        items: model.providerURLBinding(provider),
                        placeholder: "http://127.0.0.1:8088/v1",
                        defaultNew: provider == "omlx"
                            ? "http://127.0.0.1:8088/v1"
                            : "http://127.0.0.1:\(provider == "ollama" ? "11434" : "1234")/v1"
                    )
                }
            }
            .id("provider-urls-\(model.uiRevision)")
            sectionHeader("Custom endpoints")
            EditableStringList(
                items: $model.document.discovery.stringArray("custom_endpoints"),
                placeholder: "http://127.0.0.1:8080/v1",
                defaultNew: "http://127.0.0.1:8080/v1"
            )
            .id("endpoints-\(model.uiRevision)")
        }
    }

    private var swarmTab: some View {
        VStack(alignment: .leading, spacing: 12) {
            sectionHeader("Swarm")
            Toggle("mDNS discovery", isOn: $model.document.swarm.bool("mdns", default: true))
            Toggle("Subnet scan at startup", isOn: $model.document.swarm.bool("subnet_scan"))
            Text("Probes the LAN for agents on :11400 when the agent starts. Recommended when listening on 0.0.0.0.")
                .font(.caption).foregroundStyle(.secondary)
            HStack {
                Text("Heartbeat (s)")
                TextField(
                    "10",
                    value: $model.document.swarm.double("heartbeat_interval_s", default: 10),
                    format: .number
                )
                .frame(width: 80)
            }
            // require_token_for_inference/peer_stale_after_s/rediscover_interval_s:
            // newly exposed via the schema (docs/config-schema-rewrite-plan.md
            // §5 phase 4) — the old typed SwarmSection never modeled these,
            // so there's no prior behavior to preserve; render generically.
            if let newSwarmFields = model.configSchema?.sections["swarm"]?.fields
                .filter({ ["require_token_for_inference", "peer_stale_after_s", "rediscover_interval_s"].contains($0.name) })
            {
                SchemaFormView(fields: newSwarmFields, draft: $model.document.swarm)
            }
            Toggle("Require cluster token", isOn: $model.requireClusterToken)
            Text("Default: open trusted home LAN. Enable to require pairing on untrusted networks.")
                .font(.caption)
                .foregroundStyle(.secondary)
            if model.requireClusterToken {
                SecureField("Cluster token (manual override)", text: $model.document.swarm.string("cluster_token"))
                if let joinCommand = model.joinCommandText() {
                    HStack(alignment: .top) {
                        Text(joinCommand)
                            .font(.system(.caption, design: .monospaced))
                            .textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        Button("Copy join command") { model.copyJoinCommand() }
                    }
                } else if model.document.isLanMode {
                    Text("Save and restart the agent to show the join command with your LAN URL.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            sectionHeader("Subnet CIDRs")
            EditableStringList(
                items: $model.document.swarm.stringArray("subnet_cidrs"),
                placeholder: "10.0.0.0/24",
                defaultNew: "10.0.0.0/24"
            )
            .id("cidrs-\(model.uiRevision)")
            sectionHeader("Static peers")
            EditableStringList(
                items: $model.document.swarm.stringArray("peers"),
                placeholder: "http://10.0.0.32:11400",
                defaultNew: "http://127.0.0.1:11400"
            )
            .id("swarm-peers-\(model.uiRevision)")
        }
    }

    private var routingTab: some View {
        VStack(alignment: .leading, spacing: 12) {
            sectionHeader("Routing")
            Picker("Default strategy", selection: $model.document.routing.default_strategy) {
                ForEach(SettingsViewModel.strategies, id: \.self) { Text($0).tag($0) }
            }
            Toggle("Allow remote backends", isOn: $model.document.routing.allow_remote)
            Toggle("Require same model for batch shard", isOn: $model.document.routing.require_same_model_for_shard)
            sectionHeader("Routing policies")
            Text(
                "First matching policy applies. Cloud routing requires allow_cloud on an explicit policy row."
            )
            .font(.caption)
            .foregroundStyle(.secondary)
            actionButtons {
                Button("Add routing policy") { model.addRoutingPolicy() }
            }
            ForEach(model.document.routing.policies.indices, id: \.self) { index in
                routingPolicyEditor(index: index)
            }
            sectionHeader("Backend overrides")
            actionButtons {
                Button("Add backend override") { model.addBackendOverride() }
            }
            ForEach(model.document.routing.backends.indices, id: \.self) { index in
                // No `.id(array[index]...)` here: that subscript runs with
                // stale indices after the array shrinks and traps.
                backendOverrideEditor(index: index)
            }
            sectionHeader("Model pools")
            Text(
                "Host-scoped catch-all: listed hosts accept any requested model name, bypassing model_aliases matching, as long as they serve one of the pool's models."
            )
            .font(.caption)
            .foregroundStyle(.secondary)
            actionButtons {
                Button("Add model pool") { model.addModelPool() }
            }
            ForEach(Array(model.document.routing.model_pools.keys.sorted()), id: \.self) { name in
                modelPoolEditor(name: name)
            }
        }
    }

    /// routing.model_pools is a same-day-added feature with no prior
    /// Swift UI (docs/config-schema-rewrite-plan.md §5 phase 4) — its
    /// editor is fully generic (dict of dynamic entries, arbitrary
    /// user-typed keys), unlike routingPolicyEditor/backendOverrideEditor
    /// below which stay hand-tuned for their existing typed structs.
    @ViewBuilder
    private func modelPoolEditor(name: String) -> some View {
        let entryBinding = Binding<[String: JSONValue]>(
            get: { model.document.routing.model_pools[name]?.objectValue ?? [:] },
            set: { model.document.routing.model_pools[name] = .object($0) }
        )
        let poolFields = model.configSchema?.sections["routing"]?.fields
            .first(where: { $0.name == "model_pools" })?.itemSchema ?? []
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(name.isEmpty ? "(unnamed pool)" : name).font(.caption.weight(.medium))
                Spacer()
                Button(role: .destructive) {
                    model.document.routing.model_pools.removeValue(forKey: name)
                } label: {
                    Image(systemName: "minus.circle")
                }
                .buttonStyle(.borderless)
            }
            SchemaFormView(fields: poolFields, draft: entryBinding)
        }
        .padding(8)
        .background(Color.gray.opacity(0.08))
        .clipShape(RoundedRectangle(cornerRadius: 6))
    }

    private var cloudTab: some View {
        VStack(alignment: .leading, spacing: 12) {
            sectionHeader("Cloud")
            CloudSettingsView(model: model)
        }
    }

    private var uiTab: some View {
        // Schema-driven (docs/config-schema-rewrite-plan.md §5 phase 4) —
        // field order/labels/widgets come from ConfigStore.loadSchema()
        // instead of a hand-typed UiSection struct. `overrides` covers
        // what the schema can't express: the log-dir placeholder and
        // check_for_updates_automatically's poll-timer side effect.
        VStack(alignment: .leading, spacing: 12) {
            sectionHeader("\(AppBranding.displayName) app")
            LoginItemSettings()
            if let uiFields = model.configSchema?.sections["ui"]?.fields {
                SchemaFormView(
                    fields: uiFields,
                    draft: $model.document.ui,
                    overrides: [
                        "auto_start_on_launch": SchemaFieldOverride(
                            label: "Auto-start agent on launch"
                        ),
                        "check_for_updates_automatically": SchemaFieldOverride(
                            label: "Check for updates automatically",
                            onChange: { value in
                                if value.boolValue == true {
                                    updateController.restartPollingIfNeeded()
                                } else {
                                    updateController.stopPolling()
                                }
                            }
                        ),
                        "log_dir": SchemaFieldOverride(label: "Log directory", placeholder: "default"),
                    ]
                )
            } else {
                Text("Config schema unavailable — reload Settings to retry.")
                    .foregroundStyle(.secondary)
            }
            gridRow("Config file", AppConfig.defaultConfigPath().path)
        }
    }

    private var logsTab: some View {
        VStack(alignment: .leading, spacing: 12) {
            sectionHeader("Agent log")
            if !model.agentReachable {
                Label("Agent unreachable — start the agent to load logs.", systemImage: "exclamationmark.triangle")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            if let logs = model.agentLogs {
                gridRow("Log directory", logs.logDir)
                gridRow("Log file", logs.logFile)
                gridRow(
                    "Size",
                    logs.exists ? "\(logs.sizeBytes) bytes" : "File not created yet"
                )
                if logs.truncated {
                    Text("Showing the last 200 lines.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                ScrollView {
                    Text(logs.tail.joined(separator: "\n"))
                        .font(.system(.caption, design: .monospaced))
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .textSelection(.enabled)
                        .padding(8)
                }
                .frame(minHeight: 220)
                .background(.quaternary.opacity(0.25))
                .clipShape(RoundedRectangle(cornerRadius: 8))
                actionButtons {
                    Button("Refresh") { Task { await model.fetchLogs() } }
                    Button("Reveal in Finder") { revealLogFile(logs) }
                    Button("Open in Console") { openLogInConsole(logs) }
                }
            } else {
                actionButtons {
                    Button("Load logs") { Task { await model.fetchLogs() } }
                        .disabled(!model.agentReachable)
                }
            }
        }
        .task(id: tab) {
            if tab == "logs" {
                await model.fetchLogs()
            }
        }
    }

    private func revealLogFile(_ logs: AgentLogsPayload) {
        let fileURL = URL(fileURLWithPath: logs.logFile)
        let dirURL = URL(fileURLWithPath: logs.logDir, isDirectory: true)
        if FileManager.default.fileExists(atPath: fileURL.path) {
            NSWorkspace.shared.activateFileViewerSelecting([fileURL])
        } else {
            NSWorkspace.shared.open(dirURL)
        }
    }

    private func openLogInConsole(_ logs: AgentLogsPayload) {
        let fileURL = URL(fileURLWithPath: logs.logFile)
        if FileManager.default.fileExists(atPath: fileURL.path) {
            NSWorkspace.shared.open(fileURL)
        } else {
            NSWorkspace.shared.open(URL(fileURLWithPath: logs.logDir, isDirectory: true))
        }
    }

    private var toolsTab: some View {
        VStack(alignment: .leading, spacing: 12) {
            sectionHeader("CLI actions")
            actionButtons {
                Button("Run doctor") { model.runDoctor() }
                Button("Run test") { model.runTest() }
                Button("Enable gateway") { model.runGateway() }
            }
            if model.doctorOK && model.doctorIssues.isEmpty && model.message == nil {
                Label("Run doctor to check configuration", systemImage: "info.circle")
                    .font(.caption).foregroundStyle(.secondary)
            }
            ForEach(model.doctorIssues) { issue in
                VStack(alignment: .leading, spacing: 4) {
                    Text(issue.title).font(.headline)
                    Text(issue.fix).font(.caption).foregroundStyle(.secondary)
                }
                .padding(.vertical, 4)
            }
        }
    }

    @ViewBuilder
    private func actionButtons(@ViewBuilder content: () -> some View) -> some View {
        HStack {
            content()
        }
        .buttonStyle(.bordered)
        .disabled(model.isLoading)
    }

    /// Bounds-safe binding: SwiftUI can re-evaluate stale ForEach children
    /// after the array shrinks (reload / remove / doctor refresh); a direct
    /// `$array[index]` subscript then traps (observed crash:
    /// Array._checkSubscript via Binding getter in routingPolicyEditor).
    private func safePolicyBinding(
        _ index: Int
    ) -> Binding<NetllmConfigDocument.RoutingPolicy> {
        Binding(
            get: {
                let rows = model.document.routing.policies
                return rows.indices.contains(index)
                    ? rows[index] : NetllmConfigDocument.RoutingPolicy()
            },
            set: { newValue in
                guard model.document.routing.policies.indices.contains(index)
                else { return }
                model.document.routing.policies[index] = newValue
            }
        )
    }

    private func safeOverrideBinding(
        _ index: Int
    ) -> Binding<NetllmConfigDocument.BackendOverride> {
        Binding(
            get: {
                let rows = model.document.routing.backends
                return rows.indices.contains(index)
                    ? rows[index] : NetllmConfigDocument.BackendOverride()
            },
            set: { newValue in
                guard model.document.routing.backends.indices.contains(index)
                else { return }
                model.document.routing.backends[index] = newValue
            }
        )
    }

    @ViewBuilder
    private func routingPolicyEditor(index: Int) -> some View {
        let binding = safePolicyBinding(index)
        VStack(alignment: .leading, spacing: 6) {
            TextField("Name", text: binding.name)
            TextField("Model prefix", text: binding.model_prefix)
            Picker("API format", selection: Binding(
                get: { binding.wrappedValue.api_format ?? "" },
                set: { binding.wrappedValue.api_format = $0.isEmpty ? nil : $0 }
            )) {
                Text("Any").tag("")
                Text("openai").tag("openai")
                Text("anthropic").tag("anthropic")
            }
            Picker("Strategy", selection: Binding(
                get: { binding.wrappedValue.strategy ?? "" },
                set: { binding.wrappedValue.strategy = $0.isEmpty ? nil : $0 }
            )) {
                Text("Default").tag("")
                ForEach(SettingsViewModel.strategies, id: \.self) { Text($0).tag($0) }
            }
            TextField("Prefer provider", text: Binding(
                get: { binding.wrappedValue.prefer_provider ?? "" },
                set: { binding.wrappedValue.prefer_provider = $0.isEmpty ? nil : $0 }
            ))
            Toggle("Allow cloud", isOn: binding.allow_cloud)
            Toggle("Enabled", isOn: binding.enabled)
            Button("Remove", role: .destructive) {
                let idx = index
                Task { @MainActor in
                    model.removeRoutingPolicy(at: idx)
                }
            }
        }
        .padding(8)
        .background(.quaternary.opacity(0.25))
        .clipShape(RoundedRectangle(cornerRadius: 6))
    }

    @ViewBuilder
    private func backendOverrideEditor(index: Int) -> some View {
        let binding = safeOverrideBinding(index)
        VStack(alignment: .leading, spacing: 6) {
            TextField("Base URL", text: binding.base_url)
            TextField("Provider", text: binding.provider)
            TextField("API key env", text: binding.api_key_env)
            Toggle("Enabled", isOn: binding.enabled)
            Toggle("Local", isOn: binding.local)
            Button("Remove", role: .destructive) {
                let idx = index
                Task { @MainActor in
                    model.removeBackendOverride(at: idx)
                }
            }
        }
        .padding(8)
        .background(.quaternary.opacity(0.25))
        .clipShape(RoundedRectangle(cornerRadius: 6))
    }

    private func modelList(_ rows: [ModelRow], empty: String) -> some View {
        Group {
            if rows.isEmpty {
                Text(empty).foregroundStyle(.secondary)
            } else {
                ForEach(rows) { row in
                    HStack {
                        Text(row.model)
                        Spacer()
                        Text("\(row.provider) · \(row.scope)").font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
        }
    }

    private func backendRow(_ backend: BackendStatus) -> some View {
        HStack(alignment: .top) {
            statusDot(backend.health == "online")
            VStack(alignment: .leading, spacing: 2) {
                Text("\(backend.provider) — \(backend.health)")
                    .font(.headline)
                Text(backend.baseURL).font(.caption).foregroundStyle(.secondary)
                Text("\(backend.modelCount) models · in-flight \(backend.inFlight) · \(backend.local ? "local" : "remote")")
                    .font(.caption2)
            }
        }
        .padding(.vertical, 4)
    }

    private func peerRow(_ peer: PeerStatus) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("\(peer.agentId) @ \(peer.hostname)").font(.headline)
            Text("\(peer.listenURL) · \(peer.role)").font(.caption).foregroundStyle(.secondary)
        }
        .padding(.vertical, 4)
    }

    private func sectionHeader(_ title: String) -> some View {
        Text(title).font(.title3.bold()).padding(.top, 8)
    }

    private func gridRow(_ label: String, _ value: String) -> some View {
        HStack {
            Text(label).foregroundStyle(.secondary)
            Spacer()
            Text(value).textSelection(.enabled)
        }
    }

    private func statusDot(_ online: Bool) -> some View {
        Circle()
            .fill(online ? Color.green : Color.red)
            .frame(width: 8, height: 8)
            .padding(.top, 6)
    }

}

/// Stable row IDs avoid SwiftUI index-based ForEach crashes when removing items.
/// Not private: reused by SchemaFormView's list_strings widget.
struct EditableStringList: View {
    @Binding var items: [String]
    var placeholder: String
    var defaultNew: String

    @State private var rowIDs: [UUID] = []

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(rowIDs, id: \.self) { rowID in
                if let index = rowIDs.firstIndex(of: rowID), index < items.count {
                    HStack {
                        TextField(placeholder, text: binding(for: index))
                        Button(role: .destructive) { remove(rowID: rowID) } label: {
                            Image(systemName: "minus.circle")
                        }
                        .buttonStyle(.borderless)
                    }
                }
            }
            Button("Add", action: add)
        }
        .onAppear { resetRowIDs() }
        .onChange(of: items.count) { _, _ in syncRowIDs() }
    }

    private func binding(for index: Int) -> Binding<String> {
        Binding(
            get: {
                guard items.indices.contains(index) else { return "" }
                return items[index]
            },
            set: { newValue in
                guard items.indices.contains(index) else { return }
                items[index] = newValue
            }
        )
    }

    private func add() {
        items.append(defaultNew)
        rowIDs.append(UUID())
    }

    private func remove(rowID: UUID) {
        guard let index = rowIDs.firstIndex(of: rowID),
              items.indices.contains(index) else { return }
        items.remove(at: index)
        rowIDs.remove(at: index)
    }

    private func syncRowIDs() {
        if rowIDs.count < items.count {
            while rowIDs.count < items.count {
                rowIDs.append(UUID())
            }
        } else if rowIDs.count > items.count {
            rowIDs = Array(rowIDs.prefix(items.count))
        }
    }

    private func resetRowIDs() {
        rowIDs = items.map { _ in UUID() }
    }
}
