import SwiftUI

// MARK: - oMLX-inspired settings chrome

struct SettingsSectionTitle: View {
    let title: String

    var body: some View {
        Text(title.uppercased())
            .font(.caption.weight(.semibold))
            .foregroundStyle(.secondary)
            .tracking(0.6)
    }
}

struct SettingsSurfaceCard<Content: View>: View {
    @ViewBuilder var content: () -> Content

    var body: some View {
        content()
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color(nsColor: .controlBackgroundColor))
            .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .strokeBorder(Color.primary.opacity(0.06), lineWidth: 1)
            )
    }
}

struct StatusBadge: View {
    let label: String
    let isPositive: Bool

    var body: some View {
        HStack(spacing: 6) {
            Circle()
                .fill(isPositive ? Color.green : Color.red)
                .frame(width: 8, height: 8)
            Text(label)
                .font(.subheadline.weight(.medium))
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 5)
        .background((isPositive ? Color.green : Color.red).opacity(0.12))
        .clipShape(Capsule())
    }
}

struct StatMetricCard: View {
    let title: String
    let value: String
    let subtitle: String

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title.uppercased())
                .font(.caption2.weight(.semibold))
                .foregroundStyle(.secondary)
            Text(value)
                .font(.title2.weight(.semibold))
                .monospacedDigit()
            Text(subtitle)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .background(Color(nsColor: .controlBackgroundColor))
        .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .strokeBorder(Color.primary.opacity(0.06), lineWidth: 1)
        )
    }
}

struct StatusHeroCard: View {
    let version: String
    let listenURL: String
    let supervisorLabel: String
    let isRunning: Bool
    let isReachable: Bool
    var onRestart: (() -> Void)?
    var onStop: (() -> Void)?
    var onStart: (() -> Void)?

    var body: some View {
        SettingsSurfaceCard {
            HStack(alignment: .center, spacing: 16) {
                BrandImageView(size: 52)
                VStack(alignment: .leading, spacing: 6) {
                    HStack(spacing: 10) {
                        Text(AppBranding.displayName)
                            .font(.title2.weight(.semibold))
                        Text(version)
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                        StatusBadge(
                            label: supervisorLabel,
                            isPositive: isRunning
                        )
                    }
                    Text(
                        isRunning
                            ? (isReachable ? "Listening on \(listenURL)" : "Starting — \(listenURL)")
                            : "Agent not listening"
                    )
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    Text(AppBranding.tagline)
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                }
                Spacer(minLength: 8)
                HStack(spacing: 8) {
                    if isRunning {
                        Button {
                            onRestart?()
                        } label: {
                            Label("Restart", systemImage: "arrow.clockwise")
                        }
                        .buttonStyle(.bordered)
                        Button(role: .destructive) {
                            onStop?()
                        } label: {
                            Label("Stop", systemImage: "stop.fill")
                        }
                        .buttonStyle(.bordered)
                    } else {
                        Button {
                            onStart?()
                        } label: {
                            Label("Start", systemImage: "play.fill")
                        }
                        .buttonStyle(.borderedProminent)
                    }
                }
            }
        }
    }
}

struct SettingsInfoRow: View {
    let label: String
    let value: String

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(label)
                .foregroundStyle(.secondary)
                .frame(width: 120, alignment: .leading)
            Text(value)
                .textSelection(.enabled)
            Spacer()
        }
        .font(.subheadline)
    }
}

enum AppVersionInfo {
    static var short: String {
        Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "0.2.1"
    }

    static var build: String {
        Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? "0"
    }

    static var display: String { "\(short) (\(build))" }

    static var platformLine: String {
        let os = ProcessInfo.processInfo.operatingSystemVersion
        let osName = "macOS \(os.majorVersion).\(os.minorVersion)"
        #if arch(arm64)
        return "Apple Silicon · \(osName)"
        #else
        return "Intel · \(osName)"
        #endif
    }
}

struct UpdateBannerCard: View {
    @Bindable var controller: UpdateController

    var body: some View {
        SettingsSurfaceCard {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    SettingsSectionTitle(title: "Updates")
                    Spacer()
                    Button("Check for Updates") {
                        Task { await controller.checkOnce(force: true) }
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                }

                switch controller.state {
                case .idle:
                    Text("You're on the latest version (v\(AppVersionInfo.short)).")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                case .checking:
                    HStack(spacing: 8) {
                        ProgressView().controlSize(.small)
                        Text("Checking for updates…")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                    }
                case .available(let release):
                    if release.hasDMGAsset {
                        Text("Update available: v\(release.version) (you have v\(AppVersionInfo.short)).")
                            .font(.subheadline)
                    } else {
                        Text("Update available: v\(release.version) — no macOS DMG on this release yet. Open the release page to download when available.")
                            .font(.subheadline)
                    }
                    updateActions(release: release, readyDMG: nil)
                case .downloading(let progress):
                    HStack(spacing: 8) {
                        if let progress {
                            ProgressView(value: progress)
                                .controlSize(.small)
                                .frame(maxWidth: 120)
                            Text(String(format: "Downloading update… %.0f%%", progress * 100))
                                .font(.subheadline)
                                .foregroundStyle(.secondary)
                        } else {
                            ProgressView().controlSize(.small)
                            Text("Downloading update…")
                                .font(.subheadline)
                                .foregroundStyle(.secondary)
                        }
                    }
                case .readyToInstall(_, let release):
                    Text("Download complete — ready to install v\(release.version).")
                        .font(.subheadline)
                    updateActions(release: release, readyDMG: true)
                case .installing:
                    HStack(spacing: 8) {
                        ProgressView().controlSize(.small)
                        Text("Installing update…")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                    }
                case .failed(let message):
                    Label(message, systemImage: "exclamationmark.triangle.fill")
                        .font(.subheadline)
                        .foregroundStyle(.red)
                }
            }
        }
    }

    @ViewBuilder
    private func updateActions(release: GitHubRelease, readyDMG: Bool?) -> some View {
        HStack(spacing: 8) {
            if readyDMG == true, InstallLocation.canAutoInstall() {
                Button("Install and Quit") {
                    Task { await controller.installFromReadyState() }
                }
                .buttonStyle(.borderedProminent)
            } else if readyDMG == nil {
                if release.hasDMGAsset, InstallLocation.canAutoInstall() {
                    Button("Download Update") {
                        controller.downloadUpdate(release: release)
                    }
                    .buttonStyle(.borderedProminent)
                } else {
                    Button(release.hasDMGAsset ? "Download in Browser" : "Open Release Page") {
                        Task { @MainActor in
                            controller.openDownloadInBrowser(for: release)
                        }
                    }
                    .buttonStyle(.borderedProminent)
                }
            } else {
                Button("Open Download") {
                    Task { @MainActor in
                        controller.openDownloadInBrowser(for: release)
                    }
                }
                .buttonStyle(.borderedProminent)
            }
            Button("Release Notes") {
                Task { @MainActor in
                    controller.openReleaseNotes(for: release)
                }
            }
            .buttonStyle(.bordered)
        }
    }
}
