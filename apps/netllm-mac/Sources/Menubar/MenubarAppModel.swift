import AppKit
import Foundation
import Observation

struct MenubarCallbacks {
    var openSettings: () -> Void
    var openAbout: () -> Void
    var openLogFile: () -> Void
    var openLogFolder: () -> Void
}

@MainActor
@Observable
final class MenubarAppModel {
    static let shared = MenubarAppModel()

    private(set) var isConfigured = false
    private var server: ServerProcess!
    private(set) var config: AppConfig!
    private var statsPoller: StatsPoller!
    private var callbacks = MenubarCallbacks(openSettings: {}, openAbout: {}, openLogFile: {}, openLogFolder: {})
    private var observers: [NSObjectProtocol] = []

    let updateController = UpdateController.shared

    private(set) var stats = StatsSnapshot()
    private(set) var telemetrySnapshot = TelemetrySnapshot()

    func updateTelemetrySnapshot(_ snapshot: TelemetrySnapshot) {
        telemetrySnapshot = snapshot
    }
    private(set) var serverState: ServerProcess.State = .stopped
    private(set) var updateRevision = 0

    var connectableHost: String {
        guard let config else { return "127.0.0.1" }
        return AppConfig.connectableHost(for: config.bindHost)
    }

    var agentPort: Int {
        config?.port ?? 11400
    }

    var serverProcess: ServerProcess? { server }

    var uiSettings: NetllmConfigDocument.UiSection {
        uiSettingsCache
    }

    private var uiSettingsCache = NetllmConfigDocument.UiSection()

    func updateUiSettings(_ ui: [String: JSONValue]) {
        let projected = NetllmConfigDocument.UiSection(ui: ui)
        uiSettingsCache = projected
        MenubarController.shared.refreshAppearance(settings: projected)
    }

    var hasOmlxAdmin: Bool {
        stats.omlxAdminURL != nil || OmlxURLs.adminURL(from: stats.backends) != nil
    }

    private init() {}

    func configure(
        server: ServerProcess,
        config: AppConfig,
        callbacks: MenubarCallbacks
    ) {
        guard !isConfigured else { return }
        self.server = server
        self.config = config
        self.callbacks = callbacks
        let host = config.bindHost == "0.0.0.0" ? "127.0.0.1" : config.bindHost
        statsPoller = StatsPoller(host: host, port: config.port)
        statsPoller.onUpdate = { [weak self] in
            Task { @MainActor in
                self?.syncFromPoller()
            }
        }
        serverState = server.state
        registerObservers()
        syncPollerRunning()
        isConfigured = true
    }

    var isRunning: Bool { server.isRunning }

    var hasUpdateBadge: Bool {
        switch updateController.state {
        case .available, .readyToInstall:
            return true
        default:
            return false
        }
    }

    var statusTitle: String {
        menubarHeaderLines.primary
    }

    var statusSubtitle: String? {
        menubarHeaderLines.secondary
    }

    private var menubarHeaderLines: (primary: String, secondary: String?) {
        MenubarStatusFormatter.headerLines(
            for: MenubarStatusFormatter.Context(
                state: server.state,
                port: config.port,
                stats: stats,
                primaryModel: telemetrySnapshot.primaryModel
            )
        )
    }

    var servingStatsStatusContext: ServingStatsStatusContext {
        ServingStatsStatusContext(
            sourceRequests: stats.sourceRequests,
            scenarioRequests: stats.scenarioRequests,
            capacityRejections: stats.capacityRejections,
            shardlessFallbacks: stats.shardlessFallbacks
        )
    }

    static func menubarStatusTitle(
        state: ServerProcess.State,
        port: Int,
        peerCount: Int = 0
    ) -> String {
        switch state {
        case .running, .unresponsive:
            var line = "Agent running · :\(port)"
            if peerCount > 0 {
                line += " · \(peerCount) peer\(peerCount == 1 ? "" : "s")"
            }
            return line
        case .starting:
            return "Agent starting…"
        case .stopping:
            return "Agent stopping…"
        case .failed(let msg):
            return "Agent failed — \(msg)"
        case .stopped:
            return "Agent stopped"
        }
    }

    func startAgent() {
        Task {
            if case .downloading = updateController.state {
                await updateController.reconcileDownloadWithDisk()
            }
            if case .failed = server.state {
                await server.reconcileListeningPort(adoptOrphan: true)
                if server.isRunning { return }
                _ = try? await server.forceRestart()
            } else {
                _ = try? server.start()
            }
        }
    }

    func stopAgent() {
        Task { await server.stop() }
    }

    func openDashboard() {
        let host = AppConfig.connectableHost(for: config.bindHost)
        NSWorkspace.shared.open(URL(string: "http://\(host):\(config.port)/ui/")!)
    }

    func openStatus() {
        let host = AppConfig.connectableHost(for: config.bindHost)
        NSWorkspace.shared.open(URL(string: "http://\(host):\(config.port)/")!)
    }

    func openOmlx() {
        let fallback = "http://127.0.0.1:8080/admin"
        let urlString = stats.omlxAdminURL
            ?? OmlxURLs.adminURL(from: stats.backends)
            ?? fallback
        guard let url = URL(string: urlString) else { return }
        NSWorkspace.shared.open(url)
    }

    func copyEnv() {
        let host = AppConfig.connectableHost(for: config.bindHost)
        ClientEnvExporter.copyToPasteboard(host: host, port: config.port)
    }

    func restartAgent() {
        Task {
            _ = try? await server.forceRestart()
        }
    }

    func toggleDrain() {
        guard isRunning else { return }
        let host = AppConfig.connectableHost(for: config.bindHost)
        let baseURL = URL(string: "http://\(host):\(config.port)")!
        let next = !stats.draining
        Task {
            let ok = await AgentAPI.setDrain(baseURL: baseURL, draining: next)
            if ok {
                statsPoller?.pollOnce()
            }
        }
    }

    func checkForUpdates() {
        Task { await updateController.checkOnce(force: true) }
    }

    func openSettings() { callbacks.openSettings() }
    func openAbout() { callbacks.openAbout() }
    func openLogFile() { callbacks.openLogFile() }
    func openLogFolder() { callbacks.openLogFolder() }

    func quitApp() {
        Task {
            await server.stop()
            NSApp.terminate(nil)
        }
    }

    private func syncFromPoller() {
        syncStatsFromPoller(statsPoller.snapshot)
    }

    func syncStatsFromPoller(_ snap: StatsSnapshot) {
        stats = snap
    }

    private func syncPollerRunning() {
        if server.isRunning {
            statsPoller.start()
        } else {
            statsPoller.stop()
        }
        syncFromPoller()
    }

    private func registerObservers() {
        observers.append(
            NotificationCenter.default.addObserver(
                forName: ServerProcess.stateDidChangeNotification,
                object: server,
                queue: .main
            ) { [weak self] _ in
                guard let self else { return }
                MainActor.assumeIsolated {
                    self.serverState = self.server.state
                    self.syncPollerRunning()
                }
            }
        )
        observers.append(
            NotificationCenter.default.addObserver(
                forName: .netllmUpdateStateDidChange,
                object: updateController,
                queue: .main
            ) { [weak self] _ in
                guard let self else { return }
                Task { @MainActor in
                    self.updateRevision += 1
                    if case .downloading = self.updateController.state {
                        await self.updateController.reconcileDownloadWithDisk()
                    }
                }
            }
        )
    }
}
