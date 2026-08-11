import SwiftUI

/// Menubar popover, design 1f. Two states share one layout: a gateway
/// leads with the mesh load split, a peer leads with a role explainer,
/// and either grows a degraded-backend card when a backend stops
/// answering. Everything below the divider is the unchanged action list —
/// no control from the previous popover was dropped.
@MainActor
struct MenubarPopoverView: View {
    @Bindable var model: MenubarAppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 12) {
                headerSection
                if model.stats.isGateway {
                    meshLoadCard
                } else {
                    roleCard
                }
                if !nodeRows.isEmpty {
                    nodeList
                }
                if let degraded = model.stats.offlineBackends.first {
                    degradedBackendCard(degraded)
                }
                primaryActions
                agentActions
                if let updateLine = model.updateController.statusLabel {
                    updateBanner(updateLine)
                } else {
                    checkForUpdatesRow
                }
            }
            .padding(DesignTokens.cardPadding)

            Divider()

            VStack(alignment: .leading, spacing: 4) {
                linksSection
                utilitySection
                Divider()
                    .padding(.vertical, 2)
                quitRow
            }
            .padding(10)
        }
        .frame(width: DesignTokens.popoverWidth)
        .accessibilityElement(children: .contain)
        .accessibilityLabel("\(AppBranding.displayName) menubar controls")
    }

    // MARK: - Header

    private var headerSection: some View {
        HStack(spacing: 11) {
            BrandImageView(size: 34)
            VStack(alignment: .leading, spacing: 2) {
                Text(AppBranding.displayName)
                    .font(.headline)
                Text(roleLine)
                    .font(.caption)
                    .foregroundStyle(statusTone)
            }
            Spacer(minLength: 0)
            Circle()
                .fill(statusTone)
                .frame(width: 8, height: 8)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(AppBranding.displayName), \(model.statusTitle)")
    }

    private var roleLine: String {
        guard model.isRunning else { return model.statusTitle }
        let host = model.stats.hostname.isEmpty
            ? model.connectableHost
            : model.stats.hostname
        if model.stats.isGateway {
            return "Coordinator · \(host) · :\(model.agentPort)"
        }
        if let gateway = model.stats.gatewayPeer {
            return "Following \(gateway.displayName) · peer"
        }
        return "Peer · \(host) · :\(model.agentPort)"
    }

    private var statusTone: Color {
        guard model.isRunning else { return DesignTokens.danger }
        return model.stats.offlineBackends.isEmpty
            ? DesignTokens.ok
            : DesignTokens.warn
    }

    // MARK: - Mesh load (gateway)

    private var meshLoadCard: some View {
        VStack(alignment: .leading, spacing: 9) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                SettingsSectionTitle(title: "Mesh load")
                Spacer(minLength: 0)
                Text("\(inFlightTotal) in flight")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(DesignTokens.muted)
            }
            meshBar
            Text(meshSummary)
                .font(.caption)
                .foregroundStyle(DesignTokens.muted)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(11)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(DesignTokens.inset)
        .clipShape(RoundedRectangle(cornerRadius: DesignTokens.radius, style: .continuous))
    }

    @ViewBuilder
    private var meshBar: some View {
        let segments = meshSegments
        if segments.isEmpty {
            RoundedRectangle(cornerRadius: 4, style: .continuous)
                .fill(DesignTokens.gridLine)
                .frame(height: 7)
        } else {
            GeometryReader { geo in
                let gaps = CGFloat(max(segments.count - 1, 0)) * 3
                let usable = max(geo.size.width - gaps, 1)
                HStack(spacing: 3) {
                    ForEach(segments) { segment in
                        RoundedRectangle(cornerRadius: 4, style: .continuous)
                            .fill(segment.color)
                            .frame(width: max(2, usable * CGFloat(segment.share)))
                    }
                }
            }
            .frame(height: 7)
        }
    }

    private var meshSummary: String {
        guard model.stats.routedRequestTotal > 0 else {
            return "No requests routed since the agent started."
        }
        let peers = model.stats.peerCount
        guard peers > 0 else {
            return "This machine served \(percentText(localShare)) of routed requests "
                + "since the agent started."
        }
        return "This machine served \(percentText(localShare)) · "
            + "\(percentText(peerShare)) to \(peers) peer\(peers == 1 ? "" : "s") — "
            + "cumulative since the agent started."
    }

    // MARK: - Role (peer)

    private var roleCard: some View {
        VStack(alignment: .leading, spacing: 6) {
            SettingsSectionTitle(title: "Your role")
            Text(roleExplainer)
                .font(.caption)
                .foregroundStyle(DesignTokens.muted)
                .fixedSize(horizontal: false, vertical: true)
            if model.stats.routedRequestTotal > 0 {
                Text("This machine served \(percentText(localShare)) of routed "
                    + "requests since the agent started.")
                    .font(.caption)
                    .foregroundStyle(DesignTokens.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(11)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(DesignTokens.inset)
        .clipShape(RoundedRectangle(cornerRadius: DesignTokens.radius, style: .continuous))
    }

    private var roleExplainer: String {
        if let gateway = model.stats.gatewayPeer {
            return "This machine is a peer. \(gateway.displayName) advertises the "
                + "gateway role; local clients still connect through this agent."
        }
        if model.stats.peerCount > 0 {
            return "This machine is a peer. No peer in the mesh advertises the "
                + "gateway role."
        }
        return "This machine is a peer with no peers discovered yet — everything "
            + "routes to local backends."
    }

    // MARK: - Per-node list

    private struct NodeRow: Identifiable {
        var id: String
        var name: String
        var detail: String
        var tone: Color
        var share: Double?
    }

    private var nodeRows: [NodeRow] {
        let stats = model.stats
        var rows: [NodeRow] = []
        for backend in stats.backends where !backend.isPeerRow {
            rows.append(
                NodeRow(
                    id: backend.id,
                    name: backend.provider,
                    detail: "\(backend.health) · \(backend.modelCount) models",
                    tone: backend.isOnline ? DesignTokens.ok : DesignTokens.warn,
                    share: stats.routedShare(forBackendID: backend.id)
                )
            )
        }
        for peer in stats.peers {
            rows.append(
                NodeRow(
                    id: peer.id,
                    name: peer.displayName,
                    detail: peerDetail(peer),
                    tone: DesignTokens.ok,
                    share: stats.routedShare(forBackendID: "peer:\(peer.agentID)")
                )
            )
        }
        return rows
    }

    /// Role, plus how this machine came to know about the peer when the peer
    /// row carries it (UI-4a `discovered_via`). "mdns" and "static" answer
    /// very different questions when a peer misbehaves, and an agent that
    /// predates the field sends nothing rather than a wrong guess.
    private func peerDetail(_ peer: PeerSnapshot) -> String {
        let role = peer.isGateway ? "gateway" : "peer"
        guard !peer.discoveredVia.isEmpty else { return role }
        return "\(role) · \(peerDiscoveryLabel(peer.discoveredVia))"
    }

    private func peerDiscoveryLabel(_ via: String) -> String {
        switch via {
        case "mdns": return "Bonjour"
        case "subnet_scan": return "subnet scan"
        case "static": return "configured"
        case "heartbeat": return "heartbeat"
        case "join": return "joined"
        default: return via
        }
    }

    private var nodeList: some View {
        VStack(alignment: .leading, spacing: 1) {
            ForEach(nodeRows) { row in
                HStack(spacing: 9) {
                    Circle()
                        .fill(row.tone)
                        .frame(width: 7, height: 7)
                    Text(row.name)
                        .font(.caption)
                        .lineLimit(1)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    Text(row.detail)
                        .font(.caption)
                        .foregroundStyle(DesignTokens.muted)
                        .lineLimit(1)
                    Text(percentText(row.share))
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(DesignTokens.muted)
                        .frame(width: 34, alignment: .trailing)
                    shareMeter(row.share)
                }
                .padding(.vertical, 4)
                .accessibilityElement(children: .combine)
                .accessibilityLabel("\(row.name), \(row.detail)")
            }
        }
    }

    private func shareMeter(_ share: Double?) -> some View {
        let width: CGFloat = 42
        return ZStack(alignment: .leading) {
            RoundedRectangle(cornerRadius: 2, style: .continuous)
                .fill(DesignTokens.gridLine)
                .frame(width: width, height: 4)
            if let share {
                RoundedRectangle(cornerRadius: 2, style: .continuous)
                    .fill(DesignTokens.accent)
                    .frame(width: max(2, width * CGFloat(clamped(share))), height: 4)
            }
        }
        .accessibilityHidden(true)
    }

    // MARK: - Degraded backend

    private func degradedBackendCard(_ backend: BackendSnapshot) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Backend \(backend.health)")
                .font(.caption.weight(.semibold))
            Text(degradedDetail(backend))
                .font(.caption)
                .foregroundStyle(DesignTokens.muted)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 7) {
                if model.hasOmlxAdmin {
                    Button("oMLX Admin") { model.openOmlx() }
                }
                Button("Dashboard") { model.openDashboard() }
            }
            .buttonStyle(.bordered)
            .controlSize(.small)
        }
        .padding(11)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(DesignTokens.warnTint)
        .clipShape(RoundedRectangle(cornerRadius: DesignTokens.radius, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: DesignTokens.radius, style: .continuous)
                .strokeBorder(DesignTokens.warn.opacity(0.3), lineWidth: 1)
        )
    }

    private func degradedDetail(_ backend: BackendSnapshot) -> String {
        var line = "\(backend.provider) at \(backend.baseURL) is \(backend.health)."
        if !backend.detail.isEmpty {
            line += " \(backend.detail)"
        }
        return line
    }

    // MARK: - Actions

    private var primaryActions: some View {
        HStack(spacing: 7) {
            Button("Copy client env") { model.copyEnv() }
                .buttonStyle(.borderedProminent)
                .frame(maxWidth: .infinity)
            Button("Dashboard") { model.openDashboard() }
                .buttonStyle(.bordered)
                .frame(maxWidth: .infinity)
        }
    }

    private var agentActions: some View {
        HStack(spacing: 8) {
            if model.isRunning {
                Button("Stop Agent") { model.stopAgent() }
                    .buttonStyle(.bordered)
                    .accessibilityHint("Stops the netllm agent subprocess")
                Button("Restart") { model.startAgent() }
                    .buttonStyle(.bordered)
                    .accessibilityHint("Restarts the netllm agent")
            } else {
                Button("Start Agent") { model.startAgent() }
                    .buttonStyle(.borderedProminent)
                    .accessibilityHint("Starts the netllm agent on the configured port")
            }
        }
    }

    private func updateBanner(_ line: String) -> some View {
        HStack(spacing: 8) {
            Image(systemName: "arrow.down.circle.fill")
                .foregroundStyle(DesignTokens.warn)
            Text(line)
                .font(.caption)
                .foregroundStyle(DesignTokens.muted)
            Spacer()
            MenubarUpdateActions(model: model)
        }
        .padding(10)
        .background(DesignTokens.warnTint)
        .netllmCardChrome()
    }

    private var checkForUpdatesRow: some View {
        HStack(spacing: 8) {
            Text("Updates")
                .font(.caption)
                .foregroundStyle(DesignTokens.muted)
            Spacer()
            Button("Check for Updates…") {
                Task { await model.updateController.checkOnce(force: true) }
            }
            .controlSize(.small)
            .buttonStyle(.bordered)
        }
    }

    private var cloudRow: some View {
        HStack(spacing: 6) {
            Circle()
                .fill(model.stats.cloudEnabled ? DesignTokens.ok : DesignTokens.muted)
                .frame(width: 6, height: 6)
            Text("Cloud")
                .font(.caption.weight(.medium))
            Spacer()
            Text(cloudSummaryText)
                .font(.caption)
                .foregroundStyle(DesignTokens.muted)
        }
        .padding(.horizontal, 9)
        .padding(.vertical, 5)
    }

    private var cloudSummaryText: String {
        guard model.stats.cloudEnabled else { return "disabled" }
        let count = model.stats.cloudEnabledProviders.count
        let providers = count == 0 ? "no providers" : "\(count) provider\(count == 1 ? "" : "s")"
        return "\(providers) · fallback=\(model.stats.cloudFallback)"
    }

    private var linksSection: some View {
        VStack(alignment: .leading, spacing: 2) {
            popoverLink("Status page", systemImage: "doc.text.magnifyingglass") {
                model.openStatus()
            }
            popoverLink("oMLX Admin", systemImage: "cpu") {
                model.openOmlx()
            }
            cloudRow
            popoverLink("Cloud Settings…", systemImage: "cloud") {
                model.openSettings()
            }
        }
    }

    private var utilitySection: some View {
        VStack(alignment: .leading, spacing: 2) {
            popoverLink("Settings…", systemImage: "gearshape") {
                model.openSettings()
            }
            popoverLink("Log file", systemImage: "doc.text") {
                model.openLogFile()
            }
            popoverLink("Log folder", systemImage: "folder") {
                model.openLogFolder()
            }
            popoverLink("About", systemImage: "info.circle") {
                model.openAbout()
            }
        }
    }

    private var quitRow: some View {
        Button("Quit \(AppBranding.displayName)") {
            model.quitApp()
        }
        .buttonStyle(.plain)
        .foregroundStyle(DesignTokens.muted)
        .padding(.horizontal, 9)
        .padding(.vertical, 5)
        .frame(maxWidth: .infinity, alignment: .leading)
        .keyboardShortcut("q", modifiers: .command)
    }

    private func popoverLink(
        _ title: String,
        systemImage: String,
        action: @MainActor @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            Label(title, systemImage: systemImage)
                .padding(.horizontal, 9)
                .padding(.vertical, 5)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .buttonStyle(.plain)
    }

    // MARK: - Derived numbers
    //
    // Every share below comes from `status.routed_requests`, which is a
    // cumulative counter since agent start — never a live rate. The copy
    // says so rather than implying a window the API does not expose.

    private struct MeshSegment: Identifiable {
        var id: String
        var share: Double
        var color: Color
    }

    private var meshSegments: [MeshSegment] {
        let stats = model.stats
        let total = stats.routedRequestTotal
        guard total > 0 else { return [] }
        let ordered = stats.routedRequests
            .filter { $0.value > 0 }
            .sorted { $0.value > $1.value }
        return ordered.map { entry in
            MeshSegment(
                id: entry.key,
                share: Double(entry.value) / Double(total),
                color: entry.key.hasPrefix("peer:") ? DesignTokens.ok : DesignTokens.accent
            )
        }
    }

    private var peerShare: Double? {
        let stats = model.stats
        let total = stats.routedRequestTotal
        guard total > 0 else { return nil }
        var routedToPeers = 0
        for entry in stats.routedRequests where entry.key.hasPrefix("peer:") {
            routedToPeers += entry.value
        }
        return Double(routedToPeers) / Double(total)
    }

    private var localShare: Double? {
        guard let share = peerShare else { return nil }
        return 1 - share
    }

    private var inFlightTotal: Int {
        model.stats.backends.reduce(0) { $0 + $1.inFlight }
    }

    private func clamped(_ share: Double) -> Double {
        min(max(share, 0), 1)
    }

    private func percentText(_ share: Double?) -> String {
        guard let share else { return "—" }
        return String(format: "%.0f%%", clamped(share) * 100)
    }
}

@MainActor
private struct MenubarUpdateActions: View {
    @Bindable var model: MenubarAppModel

    var body: some View {
        HStack(spacing: 6) {
            switch model.updateController.state {
            case .available(let release):
                if release.hasDMGAsset, InstallLocation.canAutoInstall() {
                    Button("Download") {
                        model.updateController.downloadUpdate(release: release)
                    }
                    .controlSize(.small)
                } else {
                    Button("Open") {
                        model.updateController.openDownloadInBrowser(for: release)
                    }
                    .controlSize(.small)
                }
            case .readyToInstall(_, _):
                if InstallLocation.canAutoInstall() {
                    Button("Install") {
                        Task { await model.updateController.installFromReadyState() }
                    }
                    .controlSize(.small)
                }
            case .checking:
                ProgressView()
                    .controlSize(.small)
            case .downloading(let progress):
                if let progress {
                    Text(String(format: "%.0f%%", progress * 100))
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(DesignTokens.muted)
                } else {
                    ProgressView()
                        .controlSize(.small)
                }
            case .failed:
                Button("Retry") {
                    Task { await model.updateController.checkOnce(force: true) }
                }
                .controlSize(.small)
            case .installing:
                ProgressView()
                    .controlSize(.small)
            case .idle:
                Button("Check") {
                    Task { await model.updateController.checkOnce(force: true) }
                }
                .controlSize(.small)
            }
        }
        .buttonStyle(.bordered)
    }
}
