import AppKit
import SwiftUI

@MainActor
struct SettingsWindowView: View {
    @Bindable var model: SettingsViewModel
    @Bindable var supervisor: AgentSupervisor
    @Bindable var updateController: UpdateController
    var onRestartAgent: (() -> Void)?

    @State private var tab = "home"
    @State private var networkSection = "agent"
    @State private var portText = "11400"

    /// How many alternate addresses a peer row lists before the rest are
    /// counted instead (UI-4a) — the dashboard's `PEERS_INLINE_ALTERNATES`.
    static let inlinePeerAddresses = 2

    var body: some View {
        NavigationSplitView {
            List(selection: $tab) {
                Section("Mesh") {
                    sidebarRow("Home", "house", "home", "Home — status and serving")
                    sidebarRow("Backends", "server.rack", "backends", "Backends")
                    sidebarRow("Models & pools", "cube.box", "models", "Models and pools")
                    sidebarRow("Peers", "point.3.connected.trianglepath.dotted", "peers", "Swarm peers")
                }
                Section("Config") {
                    sidebarRow("Network", "network", "network", "Agent, discovery, and swarm")
                    sidebarRow("Routing", "arrow.triangle.branch", "routing", "Routing settings")
                    sidebarRow("Cloud failover", "cloud", "cloud", "Cloud provider settings")
                    sidebarRow("Preferences", "slider.horizontal.3", "preferences", "App preferences")
                }
                Section("Tools") {
                    sidebarRow("Integrations", "link", "integrations", "Client wiring and sources")
                    sidebarRow("Logs", "doc.text", "logs", "Agent logs")
                    sidebarRow("Doctor & test", "stethoscope", "tools", "Doctor and test tools")
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
        .onChange(of: model.requestedSettingsTab) { _, newTab in
            if let newTab, !newTab.isEmpty {
                tab = newTab
                model.requestedSettingsTab = nil
            }
        }
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
        case "home", "status", "overview":
            homeTab
        case "backends": backendsTab
        case "models": modelsTab
        case "peers": peersTab
        case "network", "agent", "discovery", "swarm": networkTab
        case "routing": routingTab
        case "cloud": cloudTab
        case "preferences", "ui": preferencesTab
        case "integrations": integrationsTab
        case "logs": logsTab
        case "tools": toolsTab
        default: homeTab
        }
    }

    /// homeTab — merged Status + Serving (web Home / overview page).
    private var homeTab: some View {
        HomeTabView(
            model: model,
            supervisor: supervisor,
            updateController: updateController,
            onRestartAgent: restartAgent
        )
        .overlay(alignment: .topTrailing) {
            if model.agentReachable {
                Button(model.status?.draining == true ? "Resume" : "Drain") {
                    Task { await model.drainButton() }
                }
                .buttonStyle(.bordered)
                .padding(.top, 8)
                .padding(.trailing, 8)
            }
        }
    }

    private var networkTab: some View {
        HStack(alignment: .top, spacing: 16) {
            List(selection: $networkSection) {
                Section("Network") {
                    Text("Agent").tag("agent")
                    Text("Discovery").tag("discovery")
                    Text("Swarm").tag("swarm")
                }
            }
            .frame(width: 150)
            ScrollView {
                Group {
                    switch networkSection {
                    case "discovery": discoveryTab
                    case "swarm": swarmTab
                    default: agentTab
                    }
                }
            }
        }
    }

    private var integrationsTab: some View {
        IntegrationsTabView(model: model)
    }

    private var preferencesTab: some View {
        PreferencesTabView(model: model, updateController: updateController) {
            pushUiSettings()
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
                        .foregroundStyle(DesignTokens.dangerText)
                } else if let msg = model.message {
                    Label(msg, systemImage: "checkmark.circle.fill")
                        .font(.caption)
                        .foregroundStyle(DesignTokens.okText)
                }
            }
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.quaternary.opacity(0.35))
            .clipShape(RoundedRectangle(cornerRadius: 8))
        }
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
        // Machine-grouped, searchable list with pool membership editing
        // (docs/models-ux-plan.md B2/B3) — replaced the flat
        // routed/local dumps.
        ModelsTabView(model: model)
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
            HStack {
                Text("Max concurrency (this machine)")
                TextField("0", value: $model.document.agent.max_concurrency, format: .number.grouping(.never))
                    .frame(width: 80)
            }
            Text("Self-declared ceiling on this machine's own concurrent requests, broadcast to peers so least_load/local_spillover selection respects it. 0 = unlimited.")
                .font(.caption).foregroundStyle(.secondary)
            gridRow("Agent ID", model.document.agent.agent_id)
            gridRow("Hostname", model.document.agent.hostname)
            gridRow("Listen", model.document.agent.listen)
            // The bind address (0.0.0.0) is not what peers dial — show
            // the resolved LAN URL the live agent actually advertises.
            gridRow("LAN address", model.status?.listenURL ?? "agent not running")
            Text("Changes apply after Save + Restart Agent.")
                .font(.caption).foregroundStyle(DesignTokens.warnText)
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
                    Text(SettingsViewModel.localProviderLabel(provider))
                        .font(.caption.weight(.medium))
                    EditableStringList(
                        items: model.providerURLBinding(provider),
                        placeholder: SettingsViewModel.localProviderDefaultURL(provider),
                        defaultNew: SettingsViewModel.localProviderDefaultURL(provider)
                    )
                }
            }
            sectionHeader("Custom endpoints")
            EditableStringList(
                items: $model.document.discovery.stringArray("custom_endpoints"),
                placeholder: "http://127.0.0.1:8080/v1",
                defaultNew: "http://127.0.0.1:8080/v1"
            )
            sectionHeader("Ignored endpoints")
            Text(
                "Base URLs discovery must never register — something else on a provider's default port, a server that is not yours. Matched after normalisation, so http://host:8000 and http://host:8000/v1 are one entry."
            )
            .font(.caption)
            .foregroundStyle(.secondary)
            // discovery.ignored_urls. Same raw-dict binding as
            // custom_endpoints above: the discovery section is an untyped
            // pass-through on this side, so a list[str] needs no Swift model.
            EditableStringList(
                items: $model.document.discovery.stringArray("ignored_urls"),
                placeholder: "http://127.0.0.1:8000",
                defaultNew: "http://127.0.0.1:8000"
            )
            ForEach(model.ignoredURLsOverruledByBackends(), id: \.self) { url in
                Text("\(url) is also pinned in routing.backends — the backend wins and this entry does nothing.")
                    .font(.caption2)
                    .foregroundStyle(DesignTokens.warnText)
            }
            sectionHeader("Server API keys")
            Text(
                "Per-endpoint keys are stored in routing.backends. Global env vars still apply when no per-URL key is set."
            )
            .font(.caption)
            .foregroundStyle(.secondary)
            ForEach(model.discoveryServerRows(), id: \.url) { row in
                VStack(alignment: .leading, spacing: 4) {
                    Text("\(SettingsViewModel.localProviderLabel(row.provider)) — \(row.url)")
                        .font(.caption.weight(.medium))
                    SecureField(
                        model.discoveryServerAPIKeySet(for: row.url)
                            ? "API key (set — enter to replace)"
                            : "API key (optional)",
                        text: Binding(
                            get: { model.discoveryServerKeyDrafts[row.url] ?? "" },
                            set: { model.setDiscoveryServerKeyDraft(row.url, $0) }
                        )
                    )
                    if let env = model.localProviderAPIKeyEnv(row.provider) {
                        Text("Global fallback: \(env)")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }
            }
            if model.discoveryServerRows().isEmpty {
                Text("Pin a provider URL or custom server above to set a per-endpoint API key.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
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
            } else {
                // Visible rather than silently missing rows — a real
                // failure here (e.g. `netllm config schema` rejecting a
                // CLI flag CLIRunner always sends) was previously
                // invisible: fields just didn't render, with no
                // indication anything was wrong.
                Text("Some swarm settings unavailable — config schema failed to load.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
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
            sectionHeader("Static peers")
            EditableStringList(
                items: $model.document.swarm.stringArray("peers"),
                placeholder: "http://10.0.0.32:11400",
                defaultNew: "http://127.0.0.1:11400"
            )
        }
    }

    private var routingTab: some View {
        VStack(alignment: .leading, spacing: 12) {
            sectionHeader("Routing")
            Picker("Default strategy", selection: $model.document.routing.default_strategy) {
                ForEach(SettingsViewModel.strategies, id: \.self) { Text($0).tag($0) }
            }
            Toggle("Allow remote backends", isOn: $model.document.routing.allow_remote)
            sectionHeader("Load & health tuning")
            HStack {
                Text("Max in-flight per backend")
                TextField("0", value: $model.document.routing.max_in_flight_per_backend, format: .number.grouping(.never))
                    .frame(width: 80)
            }
            Text("Selection prefers backends under this many concurrent requests. 0 = off.")
                .font(.caption).foregroundStyle(.secondary)
            Toggle("Follow gateway strategy", isOn: $model.document.routing.follow_gateway)
            Text("Peer-role agents adopt the gateway's advertised default strategy from heartbeats instead of running their own.")
                .font(.caption).foregroundStyle(.secondary)
            HStack {
                Text("Spillover threshold (local in-flight)")
                TextField("2", value: $model.document.routing.spillover_max_local_in_flight, format: .number.grouping(.never))
                    .frame(width: 80)
            }
            Text("Serve locally while below this many local in-flight requests; at or above it, spill to a less-loaded LAN peer only.")
                .font(.caption).foregroundStyle(.secondary)
            HStack {
                Text("Health TTL (s)")
                TextField("30", value: $model.document.routing.health_ttl_s, format: .number)
                    .frame(width: 80)
            }
            HStack {
                Text("Offline retry (s)")
                TextField("10", value: $model.document.routing.offline_retry_s, format: .number)
                    .frame(width: 80)
            }
            HStack {
                Text("Max backend failures")
                TextField("3", value: $model.document.routing.max_backend_failures, format: .number.grouping(.never))
                    .frame(width: 80)
            }
            Text("Consecutive request failures before a backend is marked offline; health TTL / offline retry control how fast it's re-probed.")
                .font(.caption).foregroundStyle(.secondary)
            HStack {
                Text("Upstream connect timeout (s)")
                TextField("5", value: $model.document.routing.upstream_connect_timeout_s, format: .number)
                    .frame(width: 80)
            }
            HStack {
                Text("Upstream read timeout (s)")
                TextField("120", value: $model.document.routing.upstream_read_timeout_s, format: .number)
                    .frame(width: 80)
            }
            Text("How long the agent waits for an upstream backend to connect and to finish generating. Raise the read timeout for large local models on slow hosts.")
                .font(.caption).foregroundStyle(.secondary)
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
            sectionHeader("Model aliases")
            Text(
                "Canonical model name → provider-specific IDs, so mixed fleets (oMLX vs Ollama vs LM Studio naming) can serve one requested name."
            )
            .font(.caption)
            .foregroundStyle(.secondary)
            actionButtons {
                Button("Add model alias") { model.addModelAlias() }
            }
            ForEach(Array(model.document.routing.model_aliases.keys.sorted()), id: \.self) { name in
                modelAliasEditor(name: name)
            }
            sectionHeader("Model pools")
            Text(
                "Heterogeneous pool: members route when they serve the requested model (or alias). Substitution to another pool model happens only when no backend in the mesh serves that name (overflow)."
            )
            .font(.caption)
            .foregroundStyle(.secondary)
            actionButtons {
                Button("Add model pool") { model.addModelPool() }
            }
            ForEach(Array(model.document.routing.model_pools.keys.sorted()), id: \.self) { name in
                modelPoolEditor(name: name)
            }
            Text("Per-client source routing lives on the Integrations tab.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    /// routing.model_aliases is a same-day-added feature with no prior
    /// Swift UI, same reasoning/shape as modelPoolEditor below — dict of
    /// dynamic, user-typed keys — except a value here is a plain
    /// [String] rather than a nested object, so it renders via
    /// EditableStringList directly instead of a nested SchemaFormView.
    @ViewBuilder
    private func modelAliasEditor(name: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(name.isEmpty ? "(unnamed alias)" : name).font(.caption.weight(.medium))
                Spacer()
                Button(role: .destructive) {
                    model.document.routing.model_aliases.removeValue(forKey: name)
                } label: {
                    Image(systemName: "minus.circle")
                }
                .buttonStyle(.borderless)
            }
            EditableStringList(
                items: Binding(
                    get: { model.document.routing.model_aliases[name]?.arrayValue?.compactMap(\.stringValue) ?? [] },
                    set: { model.document.routing.model_aliases[name] = .strings($0) }
                ),
                placeholder: "llama3:8b-instruct-q4_K_M",
                defaultNew: "",
                suggestions: model.knownModelIDs
            )
        }
        .padding(8)
        .background(DesignTokens.inset)
        .clipShape(RoundedRectangle(cornerRadius: 6))
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
            .first(where: { $0.name == "model_pools" })?.itemSchema
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
            if poolFields == nil {
                // Visible rather than a silently field-less row — see the
                // matching note on the swarm tab's new-fields fallback.
                Text("Pool fields unavailable — config schema failed to load.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            SchemaFormView(
                fields: poolFields ?? [],
                draft: entryBinding,
                overrides: [
                    "hosts": SchemaFieldOverride(suggestions: model.knownHostRefs),
                    "models": SchemaFieldOverride(suggestions: model.knownModelIDs),
                ]
            )
        }
        .padding(8)
        .background(DesignTokens.inset)
        .clipShape(RoundedRectangle(cornerRadius: 6))
    }

    private var cloudTab: some View {
        VStack(alignment: .leading, spacing: 12) {
            sectionHeader("Cloud")
            CloudSettingsView(model: model)
        }
    }

    private func pushUiSettings() {
        MenubarAppModel.shared.updateUiSettings(model.document.ui)
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
                    Text(logWindowCaption(logs))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                ScrollView {
                    logWindow(logs)
                }
                .frame(minHeight: 220)
                .background(.quaternary.opacity(0.25))
                .clipShape(RoundedRectangle(cornerRadius: 8))
                actionButtons {
                    Button("Refresh") { Task { await model.fetchLogs() } }
                    Button("Reveal in Finder") { revealLogFile(logs) }
                    Button("Open in Console") { openLogInConsole(logs) }
                    if logs.downloadURL != nil {
                        Button("Download full log") { downloadFullLog(logs) }
                    }
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

    /// Structured `records[]` when the agent parsed the window for us,
    /// otherwise the raw `tail[]` blob exactly as before. An agent older than
    /// UI-11 sends only `tail`, and losing the log view entirely would be a
    /// worse trade than losing the level column.
    @ViewBuilder
    private func logWindow(_ logs: AgentLogsPayload) -> some View {
        if logs.records.isEmpty {
            Text(logs.tail.joined(separator: "\n"))
                .font(.system(.caption, design: .monospaced))
                .frame(maxWidth: .infinity, alignment: .leading)
                .textSelection(.enabled)
                .padding(8)
        } else {
            LazyVStack(alignment: .leading, spacing: 1) {
                ForEach(logs.records) { record in
                    logRecordRow(record)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .textSelection(.enabled)
            .padding(8)
        }
    }

    private func logRecordRow(_ record: AgentLogRecord) -> some View {
        HStack(alignment: .top, spacing: 6) {
            Text(record.levelLabel ?? "—")
                .font(.system(.caption2, design: .monospaced).weight(.medium))
                .foregroundStyle(logLevelColor(record.level))
                .frame(width: 58, alignment: .leading)
            Text(record.message.isEmpty ? record.raw : record.message)
                .font(.system(.caption, design: .monospaced))
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    /// nil level means the line was not formatter-produced (a traceback
    /// continuation, a bare print) — muted rather than coloured, because
    /// giving it a severity would be inventing one.
    private func logLevelColor(_ level: String?) -> Color {
        switch level {
        case "error": return DesignTokens.dangerText
        case "warn": return DesignTokens.warnText
        case "info": return DesignTokens.text
        default: return DesignTokens.muted
        }
    }

    private func logWindowCaption(_ logs: AgentLogsPayload) -> String {
        guard logs.totalLines > 0 else { return "Showing the last 200 lines." }
        return "Showing \(logs.tail.count) of \(logs.totalLines) lines."
    }

    /// Opens `download_url` in the browser rather than fetching it here: the
    /// route is admin-gated on the same loopback origin the dashboard uses,
    /// and the file is unredacted, so handing it to the user's own download
    /// flow keeps this app from holding secrets it has no reason to.
    private func downloadFullLog(_ logs: AgentLogsPayload) {
        guard let path = logs.downloadURL,
              let url = AgentHTTP.url(base: model.agentBaseURL, path: path)
        else { return }
        NSWorkspace.shared.open(url)
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
            doctorResults
        }
    }

    /// Structured `checks[]` when the agent sends them, the derived
    /// `issues[]` otherwise. Passing rows are listed too — "N checks · M
    /// passed" is the only way this panel can say what it actually verified
    /// rather than only what broke.
    @ViewBuilder
    private var doctorResults: some View {
        if !model.doctorChecks.isEmpty {
            Text(doctorSummary(model.doctorChecks))
                .font(.caption).foregroundStyle(.secondary)
            ForEach(model.doctorChecks) { check in
                doctorCheckRow(check)
            }
        } else if model.doctorOK && model.doctorIssues.isEmpty && model.message == nil {
            Label("Run doctor to check configuration", systemImage: "info.circle")
                .font(.caption).foregroundStyle(.secondary)
        } else {
            ForEach(model.doctorIssues) { issue in
                VStack(alignment: .leading, spacing: 4) {
                    Text(issue.title).font(.headline)
                    Text(issue.fix).font(.caption).foregroundStyle(.secondary)
                }
                .padding(.vertical, 4)
            }
        }
    }

    private func doctorSummary(_ checks: [DoctorCheck]) -> String {
        let passed = checks.filter(\.ok).count
        return "\(checks.count) checks · \(passed) passed"
    }

    private func doctorCheckRow(_ check: DoctorCheck) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Circle()
                .fill(doctorSeverityColor(check.severity))
                .frame(width: 8, height: 8)
                .padding(.top, 5)
            VStack(alignment: .leading, spacing: 2) {
                Text(check.title)
                    .font(check.ok ? .caption : .headline)
                if !check.detail.isEmpty, check.detail != check.title {
                    Text(check.detail).font(.caption).foregroundStyle(.secondary)
                }
                if !check.fix.isEmpty {
                    Text(check.fix).font(.caption).foregroundStyle(.secondary)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, 2)
    }

    /// `info` is a check that passed; `warn` is advisory and deliberately
    /// does NOT clear the run's top-level `ok` (an open trusted-LAN swarm
    /// has always been a note, not a failure); `error` is a real problem.
    private func doctorSeverityColor(_ severity: String) -> Color {
        switch severity {
        case "error": return DesignTokens.danger
        case "warn": return DesignTokens.warn
        default: return DesignTokens.ok
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
            SecureField(
                model.backendAPIKeyConfigured.contains(
                    SettingsViewModel.normalizeDiscoveryURL(binding.wrappedValue.base_url)
                ) ? "API key (set — enter to replace)" : "API key",
                text: binding.api_key
            )
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
            Text(peerSubtitle(peer)).font(.caption).foregroundStyle(.secondary)
            // Alternate URLs the same peer answers on (wildcard binds). Worth
            // showing because "unreachable at the address we cached" and
            // "down" look identical without them — but a peer running Docker
            // has a bridge gateway per compose network, and listing those
            // flat buried the address anyone can actually dial. Ranked by
            // the peer's own classification (UI-4a), labelled, and cut to the
            // useful few; the rest are counted, not hidden.
            let alternates = PeerAddressKind.sorted(
                peer.alsoReachableAt, kinds: peer.addressKinds)
            ForEach(alternates.prefix(Self.inlinePeerAddresses), id: \.self) { url in
                let label = PeerAddressKind.label(peer.addressKinds[url] ?? "")
                Text("also at \(url)\(label.isEmpty ? "" : " — \(label)")")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            if alternates.count > Self.inlinePeerAddresses {
                Text("+\(alternates.count - Self.inlinePeerAddresses) more addresses")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .help(alternates.dropFirst(Self.inlinePeerAddresses).joined(separator: ", "))
            }
        }
        .padding(.vertical, 4)
    }

    private func peerSubtitle(_ peer: PeerStatus) -> String {
        var line = "\(peer.listenURL) · \(peer.role)"
        if !peer.discoveredVia.isEmpty {
            line += " · via \(peer.discoveredVia)"
        }
        return line
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
            .fill(online ? DesignTokens.ok : DesignTokens.danger)
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
    /// Known-good candidates (docs/models-ux-plan.md phase A). Non-empty
    /// enables the picker menu and per-row soft validation; free typing
    /// stays allowed either way (offline hosts are legitimate values).
    var suggestions: [SchemaSuggestion] = []

    @State private var rowIDs: [UUID] = []

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(rowIDs, id: \.self) { rowID in
                if let index = rowIDs.firstIndex(of: rowID), index < items.count {
                    HStack {
                        TextField(placeholder, text: binding(for: index))
                        if isUnknownValue(at: index) {
                            Image(systemName: "exclamationmark.triangle")
                                .foregroundStyle(DesignTokens.warnText)
                                .help("Not currently known — check spelling or bring the host online.")
                        }
                        Button(role: .destructive) { remove(rowID: rowID) } label: {
                            Image(systemName: "minus.circle")
                        }
                        .buttonStyle(.borderless)
                    }
                }
            }
            HStack {
                Button("Add", action: add)
                if !unusedSuggestions.isEmpty {
                    Menu {
                        ForEach(unusedSuggestions) { suggestion in
                            Button(suggestion.label) { append(suggestion.value) }
                        }
                    } label: {
                        Image(systemName: "plus.circle")
                    }
                    .menuStyle(.borderlessButton)
                    .fixedSize()
                    .help("Add a known value")
                }
            }
        }
        .onAppear { resetRowIDs() }
        .onChange(of: items.count) { _, _ in syncRowIDs() }
    }

    private var unusedSuggestions: [SchemaSuggestion] {
        suggestions.filter { !items.contains($0.value) }
    }

    /// Soft validation only — a value no suggestion matches is warned
    /// about, never blocked (the host may simply be offline right now).
    private func isUnknownValue(at index: Int) -> Bool {
        guard !suggestions.isEmpty, items.indices.contains(index) else { return false }
        let value = items[index].trimmingCharacters(in: .whitespaces)
        guard !value.isEmpty else { return false }
        return !suggestions.contains { $0.value == value }
    }

    private func append(_ value: String) {
        items.append(value)
        rowIDs.append(UUID())
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
