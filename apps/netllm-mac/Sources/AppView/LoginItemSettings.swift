import SwiftUI

@MainActor
struct LoginItemSettings: View {
    @State private var launchAtLogin = LoginItemManager.isRegistered
    @State private var feedback: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Toggle("Launch at login", isOn: $launchAtLogin)
                .onChange(of: launchAtLogin) { _, enabled in
                    applyLoginItem(enabled)
                }
            Text(
                "Opens the menubar app at login. Agent auto-start still follows "
                    + "Settings → UI → Auto-start agent on launch when both are enabled."
            )
            .font(.caption)
            .foregroundStyle(.secondary)
            if let feedback {
                Text(feedback)
                    .font(.caption)
                    .foregroundStyle(.orange)
            }
        }
        .onAppear {
            launchAtLogin = LoginItemManager.isRegistered
        }
    }

    private func applyLoginItem(_ enabled: Bool) {
        feedback = nil
        do {
            try LoginItemManager.setRegistered(enabled)
        } catch {
            launchAtLogin = LoginItemManager.isRegistered
            feedback = error.localizedDescription
        }
    }
}
