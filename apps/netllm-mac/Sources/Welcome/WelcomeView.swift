import SwiftUI

struct WelcomeView: View {
    @State private var step = 0
    @State private var lanMode = false
    @State private var autoStart = true
    let config: AppConfig
    let onComplete: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            BrandedHeader(subtitle: "oMLX, Ollama, LM Studio · CLI: \(AppBranding.cliCommand)")

            switch step {
            case 0:
                Text("Config will be stored at:")
                Text(config.configPath.path).font(.system(.body, design: .monospaced))
                Button("Continue") { step = 1 }
            case 1:
                Toggle("Listen on LAN (0.0.0.0)", isOn: $lanMode)
                Text("Enables swarm on your network and turns on subnet scan at agent startup (Wi‑Fi often blocks mDNS). Set cluster_token in Settings for untrusted LANs.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Toggle("Start agent on launch", isOn: $autoStart)
                Text("oMLX, Ollama, and LM Studio on this Mac are found automatically — no setup required.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                HStack {
                    Button("Back") { step = 0 }
                    Button("Finish") { finish() }
                        .keyboardShortcut(.defaultAction)
                }
            default:
                EmptyView()
            }
            Spacer()
        }
        .padding(24)
        .frame(minWidth: 480, minHeight: 320)
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
