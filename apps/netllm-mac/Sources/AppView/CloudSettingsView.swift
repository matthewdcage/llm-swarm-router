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

            Text("A provider can only be enabled once its key has been verified against "
                + "the provider — Verify key does that without saving the key. Changes to "
                + "provider enable/region apply after Save. Key changes apply after Save "
                + "+ Restart Agent.")
                .font(.caption)
                .foregroundStyle(DesignTokens.warnText)
        }
    }
}

struct CloudProviderInfo {
    var id: String
    var displayName: String
    var notes: String
    var regions: [String]
    var keychainAccount: String
    /// `CloudProviderSpec.api_key_env` as served by the agent. Empty when the
    /// row came from the offline bootstrap (or from an agent too old to send
    /// it) — read `resolvedAPIKeyEnv`, never this, and the derived default
    /// applies instead.
    var apiKeyEnv: String = ""

    var resolvedAPIKeyEnv: String {
        apiKeyEnv.isEmpty ? KeychainStore.CloudKeyEnv.defaultEnvVar(for: id) : apiKeyEnv
    }
}

@MainActor
private struct CloudProviderCard: View {
    @Bindable var model: SettingsViewModel
    var provider: CloudProviderInfo

    // Key draft/feedback/catalog state lives on the view model, keyed by
    // provider id — @State here gets destroyed by the Settings detail
    // view's former `.id(uiRevision)` on every 2-second live poll, which is
    // exactly the old "typed API key disappears" bug.

    private var binding: Binding<NetllmConfigDocument.CloudProviderConfig> {
        Binding(
            get: { model.document.cloud.providers[provider.id] ?? .init() },
            set: { model.document.cloud.providers[provider.id] = $0 }
        )
    }

    private var keyBinding: Binding<String> {
        Binding(
            get: { model.cloudKeyDrafts[provider.id] ?? "" },
            set: { model.cloudKeyDrafts[provider.id] = $0 }
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
                // Disabled, not hidden: a control that vanishes reads as a
                // missing feature, a disabled one with the blocker printed
                // under it reads as a step not yet done. The agent refuses
                // the save either way (config_guards), so an operable toggle
                // here would only promise something Save would undo.
                .disabled(!model.cloudProviderCanEnable(provider.id))
            verificationRow
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
            // api_key_env and base_url had no control on either surface.
            // The agent already honours both (service/cloud.py resolves
            // provider_cfg.api_key_env ahead of the registry env var and
            // provider_cfg.base_url ahead of the registry endpoint), so
            // their absence was an oversight, not a policy — closed in the
            // Axis D phase (docs/extending/08-control-parity.md).
            TextField("API key env var", text: binding.api_key_env)
                .textFieldStyle(.roundedBorder)
            TextField("Base URL override", text: binding.base_url)
                .textFieldStyle(.roundedBorder)
            SecureField("API key", text: keyBinding)
                .textFieldStyle(.roundedBorder)
                .onSubmit { model.saveCloudKey(provider) }
            HStack {
                Button("Save key") { model.saveCloudKey(provider) }
                Button("Verify key") { model.verifyCloudProvider(provider) }
                    .disabled(
                        !model.agentReachable || model.cloudVerifying.contains(provider.id)
                    )
                Button("Clear key", role: .destructive) { model.clearCloudKey(provider) }
            }
            .buttonStyle(.bordered)
            .controlSize(.small)
            if let feedback = model.cloudKeyFeedback[provider.id] {
                Text(feedback).font(.caption2).foregroundStyle(DesignTokens.warnText)
            }
            modelsSection
        }
        .padding(8)
        .background(.quaternary.opacity(0.25))
        .clipShape(RoundedRectangle(cornerRadius: 6))
        .onAppear { model.loadCloudKeyDraftIfNeeded(provider) }
    }

    /// The credential's verified state, and the blocker when it has one.
    ///
    /// Every word is the agent's (`CloudVerification.blocker`), never
    /// composed here — the surface that ran the check is the only one that
    /// knows whether the key was refused, the endpoint was unreachable, or
    /// nobody has looked yet, and those are three different fixes.
    @ViewBuilder
    private var verificationRow: some View {
        if model.cloudVerifying.contains(provider.id) {
            HStack(spacing: 6) {
                ProgressView().controlSize(.mini)
                Text("Checking the key against \(provider.displayName)…")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        } else if let verification = model.cloudVerification(provider.id) {
            if verification.ok {
                Text("Key verified. \(verification.detail)")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                Text(verification.blocker)
                    .font(.caption2)
                    .foregroundStyle(DesignTokens.warnText)
                    .fixedSize(horizontal: false, vertical: true)
            }
        } else {
            Text(
                "Not checked from this app yet — press Verify key to check the "
                    + "credential against \(provider.displayName)."
            )
            .font(.caption2)
            .foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// Model allowlist editor: fetch the provider's full catalog from
    /// the agent, then check/uncheck to control cloud.providers.<id>.models.
    /// Empty allowlist = all models (server default).
    @ViewBuilder
    private var modelsSection: some View {
        let allowlist = binding.wrappedValue.models
        let catalog = model.cloudCatalogs[provider.id]
        Divider()
        HStack {
            Text("Models").font(.subheadline.weight(.medium))
            Spacer()
            if model.cloudCatalogFetching.contains(provider.id) {
                ProgressView().controlSize(.mini)
            }
            Button(catalog == nil ? "Fetch model list" : "Refresh model list") {
                model.fetchCloudCatalog(provider.id)
            }
            .buttonStyle(.borderless)
            .font(.caption)
            .disabled(!model.agentReachable || model.cloudCatalogFetching.contains(provider.id))
        }
        if let catalog {
            if catalog.source == "static" {
                Text(staticCatalogNote(catalog))
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if allowlist.isEmpty {
                Text("All \(catalog.models.count) models enabled (default). Uncheck any to restrict.")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            } else {
                HStack {
                    Text("\(allowlist.count) of \(catalog.models.count) models enabled.")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                    Button("Enable all") { model.resetCloudModels(provider.id) }
                        .buttonStyle(.borderless)
                        .font(.caption2)
                }
            }
            ForEach(catalogWithConfiguredExtras(catalog, allowlist: allowlist), id: \.self) { modelID in
                Toggle(modelID, isOn: Binding(
                    get: { model.cloudModelEnabled(provider.id, model: modelID) },
                    set: { model.toggleCloudModel(provider.id, model: modelID, enabled: $0) }
                ))
                .font(.caption)
            }
            Text("Model changes apply after Save + Restart Agent. Enabled models appear on the Models tab for pool assignment.")
                .font(.caption2)
                .foregroundStyle(DesignTokens.warnText)
                .fixedSize(horizontal: false, vertical: true)
        } else if !allowlist.isEmpty {
            Text("Restricted to: \(allowlist.joined(separator: ", ")). Fetch the model list to edit.")
                .font(.caption2)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// Configured models the fetched catalog doesn't list (renamed or
    /// deprecated upstream) stay visible so they can be unchecked.
    private func catalogWithConfiguredExtras(
        _ catalog: CloudModelCatalog, allowlist: [String]
    ) -> [String] {
        catalog.models + allowlist.filter { !catalog.models.contains($0) }
    }

    private func staticCatalogNote(_ catalog: CloudModelCatalog) -> String {
        switch catalog.status {
        case "no_api_key":
            return "No API key yet — showing the built-in catalog. "
                + (catalog.detail ?? "")
        case "static_catalog":
            return "This provider has no live model-list API — showing the built-in catalog."
        default:
            return "Live catalog unavailable (\(catalog.status)) — showing the built-in catalog."
        }
    }
}
