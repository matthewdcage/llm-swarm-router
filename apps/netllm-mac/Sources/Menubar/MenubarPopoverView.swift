import SwiftUI

struct MenubarPopoverView: View {
    @Bindable var model: MenubarAppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            headerSection
            statsSection
            if let updateLine = model.updateController.statusLabel {
                updateBanner(updateLine)
            } else {
                checkForUpdatesRow
            }
            agentActions
            linksSection
            utilitySection
            quitRow
        }
        .padding(DesignTokens.cardPadding)
        .frame(width: DesignTokens.popoverWidth)
        .accessibilityElement(children: .contain)
        .accessibilityLabel("\(AppBranding.displayName) menubar controls")
    }

    private var headerSection: some View {
        HStack(spacing: 12) {
            BrandImageView(size: 36)
            VStack(alignment: .leading, spacing: 4) {
                Text(AppBranding.displayName)
                    .font(.headline)
                Text(model.statusTitle)
                    .font(.subheadline)
                    .foregroundStyle(model.isRunning ? DesignTokens.ok : .secondary)
            }
            Spacer(minLength: 0)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(AppBranding.displayName), \(model.statusTitle)")
    }

    private var statsSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            SettingsSectionTitle(title: "Routing")
            HStack(spacing: 8) {
                StatMetricCard(
                    title: "Backends",
                    value: "\(model.stats.onlineBackendCount)/\(model.stats.backendCount)",
                    subtitle: "online"
                )
                StatMetricCard(
                    title: "Peers",
                    value: "\(model.stats.peerCount)",
                    subtitle: model.stats.role
                )
            }
            if !model.stats.backends.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(model.stats.backends) { backend in
                        HStack(spacing: 6) {
                            Circle()
                                .fill(backend.health == "online" ? DesignTokens.ok : Color.secondary)
                                .frame(width: 6, height: 6)
                            Text("\(backend.provider)")
                                .font(.caption.weight(.medium))
                            Spacer()
                            Text("\(backend.health) · \(backend.modelCount) models")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            }
            if let loaded = model.stats.omlxLoadedModel, !loaded.isEmpty {
                Text("oMLX loaded: \(loaded)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else if !model.stats.modelsPreview.isEmpty {
                Text("Models: \(model.stats.modelsPreview)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private func updateBanner(_ line: String) -> some View {
        HStack(spacing: 8) {
            Image(systemName: "arrow.down.circle.fill")
                .foregroundStyle(DesignTokens.warn)
            Text(line)
                .font(.caption)
                .foregroundStyle(.secondary)
            Spacer()
            MenubarUpdateActions(model: model)
        }
        .padding(10)
        .background(DesignTokens.warn.opacity(0.1))
        .netllmCardChrome()
    }

    private var checkForUpdatesRow: some View {
        HStack(spacing: 8) {
            Text("Updates")
                .font(.caption)
                .foregroundStyle(.secondary)
            Spacer()
            Button("Check for Updates…") {
                Task { await model.updateController.checkOnce(force: true) }
            }
            .controlSize(.small)
            .buttonStyle(.bordered)
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

    private var linksSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            SettingsSectionTitle(title: "Open")
            popoverLink("Dashboard", systemImage: "gauge.with.dots.needle.67percent") {
                model.openDashboard()
            }
            popoverLink("Status page", systemImage: "doc.text.magnifyingglass") {
                model.openStatus()
            }
            popoverLink("oMLX Admin", systemImage: "cpu") {
                model.openOmlx()
            }
            popoverLink("Copy client env", systemImage: "doc.on.clipboard") {
                model.copyEnv()
            }
        }
    }

    private var utilitySection: some View {
        VStack(alignment: .leading, spacing: 6) {
            SettingsSectionTitle(title: "App")
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
        .foregroundStyle(.secondary)
        .frame(maxWidth: .infinity, alignment: .leading)
        .keyboardShortcut("q", modifiers: .command)
    }

    private func popoverLink(
        _ title: String,
        systemImage: String,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            Label(title, systemImage: systemImage)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .buttonStyle(.plain)
    }
}

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
                        .foregroundStyle(.secondary)
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
