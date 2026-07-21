import SwiftUI

@MainActor
struct CloudSettingsView: View {
    @Bindable var model: SettingsViewModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(
                "Pre-configured cloud providers. API keys live in Keychain and are "
                    + "injected into the agent process only — never written to config.toml."
            )
            .font(.caption)
            .foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)

            Toggle("Cloud enabled (master switch)", isOn: $model.document.cloud.enabled)
            Picker("Fallback direction", selection: $model.document.cloud.fallback) {
                Text("cloud (local first, cloud fallback)").tag("cloud")
                Text("local (cloud first, local fallback)").tag("local")
                Text("none (no automatic fallback)").tag("none")
            }
            Toggle("Fallback enabled", isOn: $model.document.cloud.fallback_enabled)

            Text("Providers")
                .font(.title3.bold())
                .padding(.top, 8)

            ForEach(model.cloudProviders, id: \.id) { provider in
                CloudProviderCard(model: model, provider: provider)
            }

            Text("Changes to provider enable/region apply after Save. Key changes apply "
                + "after Save + Restart Agent.")
                .font(.caption)
                .foregroundStyle(.orange)
        }
    }
}

struct CloudProviderInfo {
    var id: String
    var displayName: String
    var notes: String
    var regions: [String]
    var keychainAccount: String
}

@MainActor
private struct CloudProviderCard: View {
    @Bindable var model: SettingsViewModel
    var provider: CloudProviderInfo

    @State private var keyText = ""
    @State private var feedback: String?

    private var binding: Binding<NetllmConfigDocument.CloudProviderConfig> {
        Binding(
            get: { model.document.cloud.providers[provider.id] ?? .init() },
            set: { model.document.cloud.providers[provider.id] = $0 }
        )
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(provider.displayName).font(.headline)
            if !provider.notes.isEmpty {
                Text(provider.notes)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Toggle("Enable \(provider.displayName)", isOn: binding.enabled)
            if provider.regions.count > 1 {
                Picker("Region / profile", selection: Binding(
                    get: { binding.wrappedValue.region.isEmpty ? provider.regions[0] : binding.wrappedValue.region },
                    set: { binding.wrappedValue.region = $0 }
                )) {
                    ForEach(provider.regions, id: \.self) { Text($0).tag($0) }
                }
            }
            Picker("API format", selection: Binding(
                get: { binding.wrappedValue.api_format ?? "" },
                set: { binding.wrappedValue.api_format = $0.isEmpty ? nil : $0 }
            )) {
                Text("Default").tag("")
                Text("openai").tag("openai")
                Text("anthropic").tag("anthropic")
            }
            SecureField("API key", text: $keyText)
                .textFieldStyle(.roundedBorder)
            HStack {
                Button("Save key") { saveKey() }
                Button("Clear key", role: .destructive) { clearKey() }
            }
            .buttonStyle(.bordered)
            .controlSize(.small)
            if let feedback {
                Text(feedback).font(.caption2).foregroundStyle(.orange)
            }
        }
        .padding(8)
        .background(.quaternary.opacity(0.25))
        .clipShape(RoundedRectangle(cornerRadius: 6))
        .onAppear { keyText = KeychainStore.load(account: provider.keychainAccount) ?? "" }
    }

    private func saveKey() {
        let trimmed = keyText.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty {
            KeychainStore.delete(account: provider.keychainAccount)
            feedback = "Cleared."
            return
        }
        if trimmed == "netllm-local" {
            feedback = "Use a real API key — netllm-local is the local-mesh placeholder."
            return
        }
        do {
            try KeychainStore.save(account: provider.keychainAccount, value: trimmed)
            feedback = "Saved. Restart the agent to apply."
        } catch {
            feedback = "Could not save key to Keychain."
        }
    }

    private func clearKey() {
        KeychainStore.delete(account: provider.keychainAccount)
        keyText = ""
        feedback = "Cleared. Restart the agent to drop the injected credential."
    }
}
