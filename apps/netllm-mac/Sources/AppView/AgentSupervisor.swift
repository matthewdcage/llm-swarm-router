import Foundation

@MainActor
@Observable
final class AgentSupervisor {
    private let server: ServerProcess
    private(set) var state: ServerProcess.State = .stopped
    private var observer: NSObjectProtocol?

    init(server: ServerProcess) {
        self.server = server
        state = server.state
        observer = NotificationCenter.default.addObserver(
            forName: ServerProcess.stateDidChangeNotification,
            object: server,
            queue: .main
        ) { [weak self] _ in
            guard let self else { return }
            Task { @MainActor in
                self.state = server.state
            }
        }
    }

    var isRunning: Bool { server.isRunning }

    var statusLabel: String { server.state.settingsStatusLabel }

    func start() {
        Task {
            if case .failed = server.state {
                await server.reconcileListeningPort(adoptOrphan: true)
                if server.isRunning { return }
                _ = try? await server.forceRestart()
            } else {
                _ = try? server.start()
            }
        }
    }

    func stop() {
        Task { await server.stop() }
    }

    func restart() {
        Task { try? await server.forceRestart() }
    }
}
