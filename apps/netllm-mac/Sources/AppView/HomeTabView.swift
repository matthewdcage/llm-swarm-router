import SwiftUI

@MainActor
struct HomeTabView: View {
    @Bindable var model: SettingsViewModel
    @Bindable var supervisor: AgentSupervisor
    @Bindable var updateController: UpdateController
    var onRestartAgent: () -> Void

    @State private var telemetry = TelemetrySnapshot()
    @State private var telemetryTask: Task<Void, Never>?

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text("Home")
                .font(.largeTitle.weight(.semibold))

            StatusHeroCard(
                version: AppVersionInfo.short,
                listenURL: model.status?.listenURL ?? model.agentBaseURL.absoluteString,
                supervisorLabel: heroSupervisorLabel,
                isRunning: supervisor.isRunning,
                isReachable: model.agentReachable,
                onRestart: onRestartAgent,
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

            if model.status?.draining == true {
                StatusBadge(label: "Draining — no new requests accepted here", isPositive: false)
            } else if model.status?.reachable == false {
                StatusBadge(label: "Not LAN-reachable from peers", isPositive: false)
            }

            roleBanner

            throughputPanel

            counterPanel

            warningsPanel

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

            joinCommandsSection

            UpdateBannerCard(controller: updateController)
        }
        .onAppear { startTelemetryPolling() }
        .onDisappear { stopTelemetryPolling() }
    }

    private var heroSupervisorLabel: String {
        if model.status?.draining == true {
            return "Draining"
        }
        return supervisor.statusLabel
    }

    private var roleBanner: some View {
        SettingsSurfaceCard {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text(model.status?.role == "gateway" ? "You are the coordinator" : "Following mesh routing")
                        .font(.headline)
                    if let strategy = model.status?.routingStrategy, !strategy.isEmpty {
                        Text("Strategy: \(strategy)")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                    }
                }
                Spacer()
                Button("Network settings…") { model.requestSettingsTab("network") }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
            }
        }
    }

    private var throughputPanel: some View {
        SettingsSurfaceCard {
            SettingsSectionTitle(title: "Throughput")
            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 12) {
                metricCell("req/s", formatOptional(telemetry.liveRequestsPerS.map { String(format: "%.2f", $0) }))
                metricCell("gen tok/s", formatOptionalTps(telemetry.liveGenerationTps))
                metricCell("TTFT p50", formatOptionalMs(optionalDouble(telemetry.routerLatency["ttft_p50_ms"])))
                metricCell("In-flight", telemetry.routerInFlight > 0 ? "\(telemetry.routerInFlight)" : "—")
            }
        }
    }

    private var counterPanel: some View {
        SettingsSurfaceCard {
            SettingsSectionTitle(title: "Request counters (since agent start)")
            if let status = model.status {
                keyValueGrid(status.sourceRequests, title: "By source")
                keyValueGrid(status.scenarioRequests, title: "By scenario")
                if status.capacityRejections > 0 {
                    SettingsInfoRow(label: "Capacity rejections", value: "\(status.capacityRejections)")
                }
                if status.shardlessFallbacks > 0 {
                    SettingsInfoRow(label: "Shardless fallbacks", value: "\(status.shardlessFallbacks)")
                }
                if status.sourceRequests.isEmpty && status.scenarioRequests.isEmpty
                    && status.capacityRejections == 0 && status.shardlessFallbacks == 0 {
                    Text("No attributed requests yet — route a chat through a connected client.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            } else {
                Text("Start the agent to load counters.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }

    @ViewBuilder
    private var warningsPanel: some View {
        let warnings = model.status?.peerWarnings ?? []
        if !warnings.isEmpty {
            SettingsSurfaceCard {
                SettingsSectionTitle(title: "Warnings")
                ForEach(warnings, id: \.self) { warning in
                    Label(warning, systemImage: "exclamationmark.triangle.fill")
                        .font(.caption)
                        .foregroundStyle(DesignTokens.warnText)
                }
            }
        }
    }

    private var joinCommandsSection: some View {
        SettingsSurfaceCard {
            SettingsSectionTitle(title: "Join this swarm")
            if let listen = model.status?.listenURL, !listen.isEmpty {
                SettingsInfoRow(label: "Serving on", value: listen)
            }
            if let join = model.joinCommandText() {
                VStack(alignment: .leading, spacing: 6) {
                    Text(join)
                        .font(.system(.caption, design: .monospaced))
                        .textSelection(.enabled)
                    Button("Copy join command") { model.copyJoinCommand() }
                        .buttonStyle(.bordered)
                        .controlSize(.small)
                }
            } else {
                Text("Enable LAN mode on the Network tab to advertise a join command.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
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

    private func metricCell(_ title: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title.uppercased())
                .font(.caption2.weight(.semibold))
                .foregroundStyle(.secondary)
            Text(value)
                .font(.title3.weight(.semibold))
                .monospacedDigit()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder
    private func keyValueGrid(_ map: [String: Int], title: String) -> some View {
        if !map.isEmpty {
            Text(title)
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
                .padding(.top, 4)
            ForEach(map.sorted(by: { $0.value > $1.value }).prefix(8), id: \.key) { key, count in
                SettingsInfoRow(label: key, value: CompactCountFormatter.format(count))
            }
        }
    }

    private func startTelemetryPolling() {
        telemetryTask?.cancel()
        telemetryTask = Task {
            while !Task.isCancelled {
                if let snap = await AgentAPI.telemetry(baseURL: model.agentBaseURL) {
                    telemetry = snap
                }
                try? await Task.sleep(for: .seconds(5))
            }
        }
    }

    private func stopTelemetryPolling() {
        telemetryTask?.cancel()
        telemetryTask = nil
    }

    private func formatOptional(_ value: String?) -> String { value ?? "—" }

    private func formatOptionalTps(_ value: Double?) -> String {
        guard let value else { return "—" }
        return CompactCountFormatter.formatTps(value)
    }

    private func formatOptionalMs(_ value: Double?) -> String {
        guard let value else { return "—" }
        return String(format: "%.0f ms", value)
    }

    private func optionalDouble(_ value: Any?) -> Double? {
        if value == nil || value is NSNull { return nil }
        if let value = value as? Double { return value }
        if let value = value as? NSNumber { return value.doubleValue }
        return nil
    }
}
