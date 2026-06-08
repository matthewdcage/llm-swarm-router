import SwiftUI

struct AboutView: View {
    let onClose: () -> Void

    var body: some View {
        VStack(spacing: 16) {
            BrandImageView(size: 72)
            Text(AppBranding.displayName)
                .font(.title2.weight(.semibold))
            Text("Version \(AppVersionInfo.display)")
                .font(.subheadline)
                .foregroundStyle(.secondary)
            Text(AppBranding.tagline)
                .font(.body)
                .multilineTextAlignment(.center)
            Text("CLI: \(AppBranding.cliCommand) · Dashboard: http://127.0.0.1:11400/ui/")
                .font(.caption)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
            Button("OK") { onClose() }
                .keyboardShortcut(.defaultAction)
                .padding(.top, 8)
        }
        .padding(32)
        .frame(width: 360)
    }
}
