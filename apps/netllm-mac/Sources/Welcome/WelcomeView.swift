import Foundation
import SwiftUI

/// First run, design 1g. Four steps: what this is, how this machine sits
/// in the mesh, launch behaviour, and the handoff for a second machine.
///
/// The mockup's live discovery list ("Found 3 machines already running an
/// agent") and its token/QR ticket are not renderable here: nothing scans
/// the LAN before the agent starts, and `AppConfig.save` cannot persist a
/// cluster token. Those steps render their real neutral state instead of
/// inventing peers or a token that would not work.
struct WelcomeView: View {
    @State private var step = 0
    @State private var lanMode = false
    @State private var autoStart = true
    @State private var copied = false
    let config: AppConfig
    let onComplete: () -> Void

    private static let stepCount = 4

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            BrandedHeader(subtitle: "oMLX, Ollama, LM Studio · CLI: \(AppBranding.cliCommand)")

            stepIndicator

            VStack(alignment: .leading, spacing: 14) {
                Text(stepEyebrow)
                    .font(.caption2.weight(.semibold))
                    .tracking(0.7)
                    .foregroundStyle(DesignTokens.accent)
                Text(stepTitle)
                    .font(.title2.weight(.semibold))
                Text(stepSubtitle)
                    .font(.subheadline)
                    .foregroundStyle(DesignTokens.muted)
                    .fixedSize(horizontal: false, vertical: true)

                stepBody
            }

            Spacer(minLength: 0)

            footer
        }
        .padding(26)
        .frame(minWidth: 640, minHeight: 460)
    }

    // MARK: - Chrome

    private var stepIndicator: some View {
        HStack(spacing: 6) {
            ForEach(0..<Self.stepCount, id: \.self) { index in
                RoundedRectangle(cornerRadius: 2, style: .continuous)
                    .fill(index <= step ? DesignTokens.accent : DesignTokens.gridLine)
                    .frame(height: 3)
            }
        }
        .accessibilityLabel("Step \(step + 1) of \(Self.stepCount)")
    }

    private var stepEyebrow: String {
        let names = ["Setup", "Your mesh", "On launch", "Add a machine"]
        return "Step \(step + 1) of \(Self.stepCount) · \(names[min(step, names.count - 1)])"
    }

    private var stepTitle: String {
        switch step {
        case 0: return "Local models, one endpoint"
        case 1: return lanMode ? "This machine joins the LAN mesh" : "This machine runs on its own"
        case 2: return "Start the agent automatically?"
        default: return "Add a second machine"
        }
    }

    private var stepSubtitle: String {
        switch step {
        case 0:
            return "oMLX, Ollama, LM Studio and vLLM on this Mac are found "
                + "automatically — there is nothing to register."
        case 1:
            return "Listening on the LAN lets other machines route work here, and "
                + "lets this machine spill work to them. You can change this later "
                + "in Settings → Network."
        case 2:
            return "The agent is a background process. It can start with the app, "
                + "or you can start it from the menubar when you need it."
        default:
            return "Run this on the other machine once its agent is installed. "
                + "Both machines must be on the same network."
        }
    }

    @ViewBuilder
    private var stepBody: some View {
        switch step {
        case 0:
            infoCard {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Config file")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(DesignTokens.muted)
                    Text(config.configPath.path)
                        .font(.system(.caption, design: .monospaced))
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        case 1:
            VStack(alignment: .leading, spacing: 8) {
                meshChoice(
                    isLAN: false,
                    title: "Just this Mac",
                    detail: "Listens on 127.0.0.1:\(config.port). Nothing on the "
                        + "network can reach the agent."
                )
                meshChoice(
                    isLAN: true,
                    title: "Join the LAN mesh",
                    detail: "Listens on 0.0.0.0:\(config.port), scans the subnet at "
                        + "startup and routes with local_spillover."
                )
                Text("No machines have been probed yet — discovery runs once the "
                    + "agent starts. Peers appear in Settings → Swarm.")
                    .font(.caption)
                    .foregroundStyle(DesignTokens.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        case 2:
            infoCard {
                VStack(alignment: .leading, spacing: 8) {
                    Toggle("Start agent on launch", isOn: $autoStart)
                    Text("Applies to the menubar app. Launch at login is a separate "
                        + "switch in Settings → UI.")
                        .font(.caption)
                        .foregroundStyle(DesignTokens.muted)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        default:
            VStack(alignment: .leading, spacing: 10) {
                infoCard {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Join command")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(DesignTokens.muted)
                        Text(joinCommand)
                            .font(.system(.caption, design: .monospaced))
                            .textSelection(.enabled)
                            .fixedSize(horizontal: false, vertical: true)
                        HStack(spacing: 8) {
                            Button(copied ? "Copied" : "Copy") {
                                JoinCommandExporter.copyToPasteboard(joinCommand)
                                copied = true
                            }
                            .buttonStyle(.borderedProminent)
                            .controlSize(.small)
                        }
                    }
                }
                Text(handoffNote)
                    .font(.caption)
                    .foregroundStyle(DesignTokens.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private var footer: some View {
        HStack(spacing: 10) {
            Text("Everything here is editable later in Settings.")
                .font(.caption)
                .foregroundStyle(DesignTokens.muted)
            Spacer(minLength: 0)
            if step > 0 {
                Button("Back") { step -= 1 }
                    .buttonStyle(.bordered)
            }
            if step < Self.stepCount - 1 {
                Button("Continue") { step += 1 }
                    .buttonStyle(.borderedProminent)
                    .keyboardShortcut(.defaultAction)
            } else {
                Button("Finish") { finish() }
                    .buttonStyle(.borderedProminent)
                    .keyboardShortcut(.defaultAction)
            }
        }
    }

    // MARK: - Pieces

    private func infoCard<Content: View>(
        @ViewBuilder content: () -> Content
    ) -> some View {
        content()
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(DesignTokens.inset)
            .clipShape(RoundedRectangle(cornerRadius: DesignTokens.radius, style: .continuous))
    }

    private func meshChoice(isLAN: Bool, title: String, detail: String) -> some View {
        let selected = lanMode == isLAN
        return Button {
            lanMode = isLAN
        } label: {
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: selected ? "largecircle.fill.circle" : "circle")
                    .foregroundStyle(selected ? DesignTokens.accent : DesignTokens.muted)
                VStack(alignment: .leading, spacing: 3) {
                    Text(title)
                        .font(.subheadline.weight(.semibold))
                    Text(detail)
                        .font(.caption)
                        .foregroundStyle(DesignTokens.muted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(selected ? DesignTokens.accentTint : DesignTokens.inset)
            .clipShape(RoundedRectangle(cornerRadius: DesignTokens.radius, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: DesignTokens.radius, style: .continuous)
                    .strokeBorder(
                        selected ? DesignTokens.accent : DesignTokens.border,
                        lineWidth: 1
                    )
            )
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(selected ? AccessibilityTraits.isSelected : [])
    }

    /// Tokenless join — valid while `swarm.cluster_token` is unset, which is
    /// the state this wizard leaves the config in. A token is added later
    /// from Settings → Swarm, which also rewrites this command.
    private var joinCommand: String {
        let host = lanMode ? ProcessInfo.processInfo.hostName : "127.0.0.1"
        return "\(AppBranding.cliCommand) join http://\(host):\(config.port)"
    }

    private var handoffNote: String {
        if lanMode {
            return "This swarm has no cluster token, so any machine on the network "
                + "can join. Set one in Settings → Swarm for untrusted networks."
        }
        return "This machine is set to listen on 127.0.0.1, so nothing can join "
            + "yet. Go back and pick “Join the LAN mesh” to make it reachable."
    }

    private func finish() {
        let cfg = config
        do {
            try cfg.save(
                bindHost: "127.0.0.1",
                port: cfg.port,
                autoStart: autoStart,
                lanMode: lanMode
            )
        } catch {
            NSLog("Welcome save failed: \(error)")
        }
        onComplete()
    }
}
