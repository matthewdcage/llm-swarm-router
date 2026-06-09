import SwiftUI

@MainActor
struct CloudFailoverSettings: View {
    @State private var anthropicKey = ""
    @State private var openaiKey = ""
    @State private var feedback: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(
                "Optional cloud API keys enable OpenAI or Anthropic failover when no local backend "
                    + "serves a model. The local mesh default is netllm-local; real keys are stored "
                    + "in Keychain and injected into the agent only."
            )
            .font(.caption)
            .foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)

            SecureField("Anthropic API key", text: $anthropicKey)
                .textFieldStyle(.roundedBorder)
            SecureField("OpenAI API key", text: $openaiKey)
                .textFieldStyle(.roundedBorder)

            HStack {
                Button("Save keys") { saveKeys() }
                Button("Clear keys", role: .destructive) { clearKeys() }
            }
            .buttonStyle(.bordered)

            if let feedback {
                Text(feedback)
                    .font(.caption)
                    .foregroundStyle(.orange)
            }
        }
        .onAppear { loadKeys() }
    }

    private enum CloudKeyError: Error {
        case placeholderNotAllowed
    }

    private func loadKeys() {
        anthropicKey = KeychainStore.load(account: KeychainStore.Account.anthropicAPIKey) ?? ""
        openaiKey = KeychainStore.load(account: KeychainStore.Account.openaiAPIKey) ?? ""
    }

    private func saveKeys() {
        feedback = nil
        do {
            try saveAccount(KeychainStore.Account.anthropicAPIKey, value: anthropicKey)
            try saveAccount(KeychainStore.Account.openaiAPIKey, value: openaiKey)
            feedback = "Saved. Restart the agent to apply cloud keys."
        } catch CloudKeyError.placeholderNotAllowed {
            feedback = "Use a real API key or leave the field empty. netllm-local is for local mesh only."
        } catch {
            feedback = "Could not save keys to Keychain."
        }
    }

    private func clearKeys() {
        KeychainStore.delete(account: KeychainStore.Account.anthropicAPIKey)
        KeychainStore.delete(account: KeychainStore.Account.openaiAPIKey)
        anthropicKey = ""
        openaiKey = ""
        feedback = "Cleared cloud keys. Restart the agent to drop injected credentials."
    }

    private func saveAccount(_ account: String, value: String) throws {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty {
            KeychainStore.delete(account: account)
            return
        }
        if trimmed == "netllm-local" {
            throw CloudKeyError.placeholderNotAllowed
        }
        try KeychainStore.save(account: account, value: trimmed)
    }
}
