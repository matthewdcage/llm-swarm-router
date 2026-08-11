import SwiftUI

@MainActor
struct IntegrationsTabView: View {
    @Bindable var model: SettingsViewModel

    @State private var selectedHarnessID = "cursor"

    private static let integrationClients: [(id: String, label: String, harnessId: String?)] = [
        ("cursor", "Cursor", "cursor"),
        ("claude-code", "Claude Code", "claude-code"),
        ("codex", "Codex CLI", "codex"),
        ("honcho", "Honcho", "honcho"),
        ("hermes-agent", "Hermes Agent", "hermes-agent"),
    ]

    var body: some View {
        HStack(alignment: .top, spacing: 16) {
            List(selection: $selectedHarnessID) {
                Section("Clients") {
                    ForEach(Self.integrationClients, id: \.id) { client in
                        Text(client.label).tag(client.id)
                    }
                }
            }
            .frame(width: 170)

            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    wiringPanel
                    connectedClientsPanel
                    sourcesPanel
                }
            }
        }
    }

    private var wiringPanel: some View {
        SettingsSurfaceCard {
            SettingsSectionTitle(title: "Client wiring")
            let host = AppConfig.connectableHost(for: model.document.bindHost)
            SettingsInfoRow(label: "OpenAI base", value: "http://\(host):\(model.document.port)/v1")
            SettingsInfoRow(label: "Anthropic base", value: "http://\(host):\(model.document.port)")
            SettingsInfoRow(label: "API key", value: "netllm-local")
            HStack(spacing: 8) {
                Button("Copy client env") { model.copyClientEnvToPasteboard() }
                Button("Write to ~/.zshrc") { model.appendClientEnvToShellProfile() }
            }
            .buttonStyle(.bordered)
            .controlSize(.small)
            Text("Use ./netllm connect <id> for per-client snippets with model IDs from the live catalog.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    private var connectedClientsPanel: some View {
        SettingsSurfaceCard {
            SettingsSectionTitle(title: "Connected clients")
            if let status = model.status, !status.sourceRequests.isEmpty {
                ForEach(status.sourceRequests.sorted(by: { $0.value > $1.value }), id: \.key) { key, count in
                    SettingsInfoRow(
                        label: key,
                        value: "\(CompactCountFormatter.format(count)) requests"
                    )
                }
            } else {
                Text("No attributed requests yet.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private var sourcesPanel: some View {
        VStack(alignment: .leading, spacing: 12) {
            SettingsSectionTitle(title: "Routing sources")
            Text("Per-client routing overrides. Enable harnesses from the registry below.")
                .font(.caption)
                .foregroundStyle(.secondary)
            unregisteredHarnessesSection
            actionButtons {
                Button("Add source") {
                    model.document.routing.sources.append(.object(["id": .string(""), "enabled": .bool(true)]))
                }
            }
            ForEach(model.document.routing.sources.indices, id: \.self) { index in
                sourceEditor(index: index)
            }
        }
    }

    @ViewBuilder
    private var unregisteredHarnessesSection: some View {
        let unregistered = model.harnessRegistry.filter { !$0.configured }
        if !unregistered.isEmpty {
            VStack(alignment: .leading, spacing: 4) {
                ForEach(unregistered) { harness in
                    HStack {
                        Text(harness.displayName).font(.caption)
                        Spacer()
                        Button("Add & enable") {
                            model.document.routing.sources.append(
                                .object([
                                    "id": .string(harness.id),
                                    "enabled": .bool(true),
                                    "known_id": .string(harness.id),
                                ])
                            )
                        }
                        .buttonStyle(.borderless)
                    }
                }
            }
            .padding(8)
            .background(DesignTokens.inset)
            .clipShape(RoundedRectangle(cornerRadius: 6))
        }
    }

    @ViewBuilder
    private func sourceEditor(index: Int) -> some View {
        let binding = safeSourceBinding(index)
        let sourceFields = model.configSchema?.sections["routing"]?.fields
            .first(where: { $0.name == "sources" })?.itemSchema ?? []
        let sourceId = binding.wrappedValue["id"]?.stringValue ?? ""
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(sourceId.isEmpty ? "(unnamed source)" : sourceId)
                    .font(.caption.weight(.medium))
                Spacer()
                Button(role: .destructive) {
                    guard model.document.routing.sources.indices.contains(index) else { return }
                    model.document.routing.sources.remove(at: index)
                } label: {
                    Image(systemName: "minus.circle")
                }
                .buttonStyle(.borderless)
            }
            SchemaFormView(fields: sourceFields, draft: binding)
        }
        .padding(8)
        .background(DesignTokens.inset)
        .clipShape(RoundedRectangle(cornerRadius: 6))
    }

    private func safeSourceBinding(_ index: Int) -> Binding<[String: JSONValue]> {
        Binding(
            get: {
                let rows = model.document.routing.sources
                return rows.indices.contains(index) ? (rows[index].objectValue ?? [:]) : [:]
            },
            set: { newValue in
                guard model.document.routing.sources.indices.contains(index) else { return }
                model.document.routing.sources[index] = .object(newValue)
            }
        )
    }

    @ViewBuilder
    private func actionButtons(@ViewBuilder content: () -> some View) -> some View {
        HStack { content() }
            .buttonStyle(.bordered)
            .disabled(model.isLoading)
    }
}
