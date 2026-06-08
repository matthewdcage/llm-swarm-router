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
