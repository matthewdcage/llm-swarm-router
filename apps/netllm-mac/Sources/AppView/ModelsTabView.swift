import SwiftUI

/// Models tab (docs/models-ux-plan.md B2/B3): one machine-grouped,
/// collapsible, searchable list replacing the old flat routed/local
/// dumps. Groups come from live /netllm/v1/status backends — local
/// backends under this Mac's hostname, each peer's backends under
/// "<hostname> (<agent_id>)" via BackendStatus.agentId. Rows carry pool
/// membership badges and an inline add/remove-pool menu that edits the
/// same document.routing.model_pools draft the Routing tab binds to.
/// Per-model activity metrics are deliberately absent (phase C — the
/// server only tracks per-backend counters today; don't fake them).
@MainActor
struct ModelsTabView: View {
    @Bindable var model: SettingsViewModel

    // Filter/collapse state lives on the view model (not @State): the
    // Settings detail view is keyed by `.id(uiRevision)`, which would
    // reset view-local state on every 2-second live poll.
    private var searchText: String { model.modelsSearchText }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Models").font(.title3.bold()).padding(.top, 8)
            searchField
            if groups.isEmpty {
                emptyState
            } else {
                ForEach(visibleGroups) { group in
                    groupView(group)
                }
                if isFiltering && visibleGroups.isEmpty {
                    Text("No models match “\(searchText)”.")
                        .foregroundStyle(.secondary)
                }
            }
            footer
        }
    }

    // MARK: - Grouping

    private struct ModelRowItem: Identifiable {
        var id: String
        var model: String
        var provider: String
    }

    private struct MachineGroup: Identifiable {
        var id: String
        var title: String
        var subtitle: String
        var online: Bool
        var modelCount: Int
        var inFlight: Int
        var rows: [ModelRowItem]
    }

    private var groups: [MachineGroup] {
        guard let status = model.status else { return [] }
        var locals: [BackendStatus] = []
        // Keyed by agent id (or base_url when a peer predates agent_id
        // in its status payload) so multi-backend machines fold into one
        // group; ordered by first appearance.
        var peerBuckets: [(key: String, backends: [BackendStatus])] = []
        for backend in status.backends {
            if backend.local {
                locals.append(backend)
            } else {
                let key = backend.agentId.isEmpty ? backend.baseURL : backend.agentId
                if let index = peerBuckets.firstIndex(where: { $0.key == key }) {
                    peerBuckets[index].backends.append(backend)
                } else {
                    peerBuckets.append((key, [backend]))
                }
            }
        }
        var result: [MachineGroup] = []
        if !locals.isEmpty {
            let title = status.hostname.isEmpty ? "This Mac" : status.hostname
            result.append(makeGroup(id: "local", title: title, backends: locals))
        }
        for bucket in peerBuckets {
            let peer = status.peers.first { $0.agentId == bucket.key }
            let title: String
            if let peer, !peer.hostname.isEmpty {
                title = "\(peer.hostname) (\(peer.agentId))"
            } else if bucket.backends.first?.agentId.isEmpty == false {
                title = bucket.key
            } else {
                title = bucket.backends.first?.baseURL ?? bucket.key
            }
            result.append(makeGroup(id: "peer-\(bucket.key)", title: title, backends: bucket.backends))
        }
        return result
    }

    private func makeGroup(id: String, title: String, backends: [BackendStatus]) -> MachineGroup {
        var seenModels = Set<String>()
        var rows: [ModelRowItem] = []
        for backend in backends {
            for served in backend.models where seenModels.insert(served).inserted {
                rows.append(
                    ModelRowItem(
                        id: "\(id)-\(backend.id)-\(served)",
                        model: served,
                        provider: backend.provider
                    )
                )
            }
        }
        rows.sort { $0.model.localizedCaseInsensitiveCompare($1.model) == .orderedAscending }
        var providers: [String] = []
        for backend in backends where !providers.contains(backend.provider) {
            providers.append(backend.provider)
        }
        return MachineGroup(
            id: id,
            title: title,
            subtitle: providers.joined(separator: " · "),
            online: backends.contains { $0.health == "online" },
            modelCount: rows.count,
            inFlight: backends.reduce(0) { $0 + $1.inFlight },
            rows: rows
        )
    }

    // MARK: - Filtering

    private var isFiltering: Bool {
        !searchText.trimmingCharacters(in: .whitespaces).isEmpty
    }

    private var visibleGroups: [MachineGroup] {
        guard isFiltering else { return groups }
        let needle = searchText.trimmingCharacters(in: .whitespaces)
        return groups.compactMap { group in
            var filtered = group
            let titleMatches = group.title.localizedCaseInsensitiveContains(needle)
                || group.subtitle.localizedCaseInsensitiveContains(needle)
            if !titleMatches {
                filtered.rows = group.rows.filter {
                    $0.model.localizedCaseInsensitiveContains(needle)
                        || $0.provider.localizedCaseInsensitiveContains(needle)
                }
            }
            // Hide groups a filter empties (plan B2).
            return filtered.rows.isEmpty ? nil : filtered
        }
    }

    private func isExpanded(_ group: MachineGroup) -> Binding<Bool> {
        Binding(
            // Active filter auto-expands every surviving group (plan B2).
            get: { isFiltering || !model.modelsCollapsedGroups.contains(group.id) },
            set: { expanded in
                if expanded {
                    model.modelsCollapsedGroups.remove(group.id)
                } else {
                    model.modelsCollapsedGroups.insert(group.id)
                }
            }
        )
    }

    // MARK: - Subviews

    private var searchField: some View {
        HStack(spacing: 6) {
            Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
            TextField("Filter by model, provider, or host", text: $model.modelsSearchText)
                .textFieldStyle(.plain)
            if isFiltering {
                Button {
                    model.modelsSearchText = ""
                } label: {
                    Image(systemName: "xmark.circle.fill")
                }
                .buttonStyle(.borderless)
                .accessibilityLabel("Clear filter")
            }
        }
        .padding(8)
        .background(.quaternary.opacity(0.35))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private func groupView(_ group: MachineGroup) -> some View {
        DisclosureGroup(isExpanded: isExpanded(group)) {
            VStack(alignment: .leading, spacing: 0) {
                ForEach(group.rows) { row in
                    modelRow(row)
                    if row.id != group.rows.last?.id {
                        Divider()
                    }
                }
            }
            .padding(.top, 4)
        } label: {
            groupHeader(group)
        }
        .padding(10)
        .background(Color.gray.opacity(0.08))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private func groupHeader(_ group: MachineGroup) -> some View {
        HStack(spacing: 8) {
            Circle()
                .fill(group.online ? Color.green : Color.red)
                .frame(width: 8, height: 8)
                .accessibilityLabel(group.online ? "Online" : "Offline")
            VStack(alignment: .leading, spacing: 1) {
                Text(group.title).font(.headline)
                if !group.subtitle.isEmpty {
                    Text(group.subtitle).font(.caption).foregroundStyle(.secondary)
                }
            }
            Spacer()
            Text(headerSummary(group))
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    private func headerSummary(_ group: MachineGroup) -> String {
        var parts = ["\(group.modelCount) model\(group.modelCount == 1 ? "" : "s")"]
        if group.inFlight > 0 {
            parts.append("\(group.inFlight) in flight")
        }
        return parts.joined(separator: " · ")
    }

    private func modelRow(_ row: ModelRowItem) -> some View {
        HStack(spacing: 8) {
            VStack(alignment: .leading, spacing: 1) {
                Text(row.model)
                Text(row.provider).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            poolBadges(for: row.model)
            poolMenu(for: row.model)
        }
        .padding(.vertical, 6)
        .contextMenu { poolMenuItems(for: row.model) }
    }

    // MARK: - Pool membership (B3)

    @ViewBuilder
    private func poolBadges(for modelID: String) -> some View {
        let pools = model.pools(containing: modelID)
        if !pools.isEmpty {
            HStack(spacing: 4) {
                ForEach(pools) { pool in
                    poolBadge(pool)
                }
            }
        }
    }

    private func poolBadge(_ pool: SettingsViewModel.ModelPoolSummary) -> some View {
        let inactiveReason = model.poolInactiveReason(pool)
        return HStack(spacing: 4) {
            Circle()
                .fill(inactiveReason == nil ? Color.green : Color.orange)
                .frame(width: 6, height: 6)
            Text(pool.name).font(.caption2)
        }
        .padding(.horizontal, 7)
        .padding(.vertical, 3)
        .background((pool.enabled ? Color.accentColor : Color.gray).opacity(0.15))
        .clipShape(Capsule())
        .help(poolBadgeHelp(pool, inactiveReason: inactiveReason))
    }

    private func poolBadgeHelp(
        _ pool: SettingsViewModel.ModelPoolSummary,
        inactiveReason: String?
    ) -> String {
        if !pool.enabled { return "Pool \(pool.name) is disabled." }
        if let inactiveReason {
            return "Pool \(pool.name) is inactive: \(inactiveReason)."
        }
        return "Pool \(pool.name) is active — a listed host is online and serves a pool model."
    }

    private func poolMenu(for modelID: String) -> some View {
        Menu {
            poolMenuItems(for: modelID)
        } label: {
            Image(systemName: "ellipsis.circle")
        }
        .menuStyle(.borderlessButton)
        .fixedSize()
        .accessibilityLabel("Pool actions for \(modelID)")
    }

    @ViewBuilder
    private func poolMenuItems(for modelID: String) -> some View {
        let candidates = model.pools(notContaining: modelID)
        let containing = model.pools(containing: modelID)
        Menu("Add to pool") {
            ForEach(candidates) { pool in
                Button(pool.name) { model.addModel(modelID, toPool: pool.name) }
            }
            if !candidates.isEmpty {
                Divider()
            }
            Button("New pool…") { model.addModelToNewPool(modelID) }
        }
        ForEach(containing) { pool in
            Button("Remove from \(pool.name)", role: .destructive) {
                model.removeModel(modelID, fromPool: pool.name)
            }
        }
    }

    // MARK: - Empty / footer

    @ViewBuilder
    private var emptyState: some View {
        if model.agentReachable {
            Text("No backends yet — start oMLX or Ollama on this Mac. The agent finds them automatically.")
                .foregroundStyle(.secondary)
        } else {
            Text("Agent not running — start it from the Status tab to load the model catalog.")
                .foregroundStyle(.secondary)
        }
    }

    private var footer: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Button("Refresh provider scan") { model.runDiscover() }
                Button("Scan LAN peers") { model.runPeersScan() }
            }
            .buttonStyle(.bordered)
            .disabled(model.isLoading)
            Text("Pool edits write routing.model_pools — press Save in the toolbar to persist. Full LAN model merge: `netllm models --lan` in terminal.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .padding(.top, 8)
    }
}
