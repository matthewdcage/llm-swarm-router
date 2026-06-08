import Foundation
import Darwin

final class ServerProcess: @unchecked Sendable {
    enum State: Equatable, Sendable {
        case stopped
        case starting
        case running(pid: Int32)
        case stopping
        case unresponsive(pid: Int32)
        case failed(message: String)
    }

    enum StartResult: Sendable {
        case started
        case alreadyRunning
        case portConflict
    }

    static let stateDidChangeNotification = Notification.Name("NetllmServerProcessStateDidChange")

    private(set) var bindAddress: String
    var host: String { AppConfig.connectableHost(for: bindAddress) }
    private(set) var port: Int
    private(set) var configPath: URL
    private let runtime: PythonRuntime
    private let logURL: URL

    private(set) var state: State = .stopped
    private var process: Process?
    private var logHandle: FileHandle?
    private var healthTask: Task<Void, Never>?
    private var consecutiveFailures = 0
    private var autoRestartCount = 0
    private var lastHealthyAt: Date?
    private var expectingExit = false

    private let healthCheckInterval: TimeInterval = 5
    private let maxHealthFailures = 3
    private let maxAutoRestarts = 3
    private let stableThreshold: TimeInterval = 60
    private let stopGraceSeconds: TimeInterval = 10

    init(runtime: PythonRuntime, bindAddress: String, port: Int, configPath: URL) {
        self.runtime = runtime
        self.bindAddress = bindAddress
        self.port = port
        self.configPath = configPath
        self.logURL = AppConfig.appSupportURL().appendingPathComponent("logs/agent.log")
    }

    var isRunning: Bool {
        if case .running = state { return true }
        if case .unresponsive = state { return true }
        return process?.isRunning == true
    }

    var pid: Int32? { process?.processIdentifier }

    @discardableResult
    func start() throws -> StartResult {
        switch state {
        case .running, .starting, .unresponsive:
            return .alreadyRunning
        default:
            break
        }
        try doStart()
        return .started
    }

    func stop() async {
        guard isRunning || state == .starting else { return }
        state = .stopping
        expectingExit = true
        cancelHealthLoop()
        if let proc = process, proc.isRunning {
            kill(proc.processIdentifier, SIGTERM)
            let deadline = Date().addingTimeInterval(stopGraceSeconds)
            while proc.isRunning && Date() < deadline {
                try? await Task.sleep(for: .milliseconds(100))
            }
            if proc.isRunning {
                kill(proc.processIdentifier, SIGKILL)
            }
        }
        process = nil
        closeLog()
        expectingExit = false
        update(.stopped)
    }

    @discardableResult
    func forceRestart() async throws -> StartResult {
        expectingExit = true
        cancelHealthLoop()
        if let proc = process, proc.isRunning {
            kill(proc.processIdentifier, SIGKILL)
        }
        process = nil
        closeLog()
        autoRestartCount = 0
        consecutiveFailures = 0
        expectingExit = false
        update(.stopped)
        return try start()
    }

    private func doStart() throws {
        try FileManager.default.createDirectory(
            at: logURL.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        if !FileManager.default.fileExists(atPath: logURL.path) {
            FileManager.default.createFile(atPath: logURL.path, contents: nil)
        }
        let handle = try FileHandle(forWritingTo: logURL)
        try handle.seekToEnd()
        logHandle = handle

        let proc = Process()
        if FileManager.default.fileExists(atPath: runtime.bundleCLIPath.path) {
            proc.executableURL = runtime.bundleCLIPath
            proc.arguments = ["serve", "-q", "--config", configPath.path]
        } else {
            proc.executableURL = URL(fileURLWithPath: "/usr/bin/env")
            proc.arguments = ["netllm", "serve", "-q", "--config", configPath.path]
        }
        proc.environment = runtime.makeEnvironment()
        proc.standardOutput = handle
        proc.standardError = handle
        proc.terminationHandler = { [weak self] term in
            DispatchQueue.main.async {
                self?.handleProcessExit(code: term.terminationStatus)
            }
        }

        update(.starting)
        try proc.run()
        process = proc
        startHealthCheckLoop()
    }

    private func handleProcessExit(code: Int32) {
        let wasExpecting = expectingExit
        expectingExit = false
        process = nil
        closeLog()
        if wasExpecting {
            update(.stopped)
            return
        }
        tryAutoRestart(reason: "Agent exited with code \(code)")
    }

    private func tryAutoRestart(reason: String) {
        if let last = lastHealthyAt, Date().timeIntervalSince(last) >= stableThreshold {
            autoRestartCount = 0
        }
        if autoRestartCount >= maxAutoRestarts {
            update(.failed(message: "\(reason). Auto-restart failed."))
            return
        }
        autoRestartCount += 1
        let backoff = TimeInterval(5 * (1 << (autoRestartCount - 1)))
        update(.starting)
        Task { @MainActor [weak self] in
            try? await Task.sleep(for: .seconds(backoff))
            guard let self, case .starting = self.state else { return }
            try? self.doStart()
        }
    }

    private func startHealthCheckLoop() {
        cancelHealthLoop()
        healthTask = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                await self.tickHealth()
                try? await Task.sleep(for: .seconds(self.healthCheckInterval))
            }
        }
    }

    private func cancelHealthLoop() {
        healthTask?.cancel()
        healthTask = nil
    }

    @MainActor
    private func tickHealth() async {
        let url = URL(string: "http://\(host):\(port)/health")!
        var request = URLRequest(url: url, timeoutInterval: 3)
        request.httpMethod = "GET"
        let healthy: Bool
        do {
            let (_, response) = try await URLSession.shared.data(for: request)
            healthy = (response as? HTTPURLResponse)?.statusCode == 200
        } catch {
            healthy = false
        }

        switch state {
        case .starting:
            if healthy {
                consecutiveFailures = 0
                lastHealthyAt = Date()
                update(.running(pid: process?.processIdentifier ?? 0))
            }
        case .running(let pid), .unresponsive(let pid):
            if healthy {
                consecutiveFailures = 0
                lastHealthyAt = Date()
                if case .unresponsive = state {
                    update(.running(pid: pid))
                }
            } else {
                consecutiveFailures += 1
                if consecutiveFailures >= maxHealthFailures, case .running = state {
                    update(.unresponsive(pid: pid))
                }
            }
        default:
            break
        }
    }

    private func update(_ next: State) {
        guard state != next else { return }
        state = next
        DispatchQueue.main.async {
            NotificationCenter.default.post(name: Self.stateDidChangeNotification, object: self)
        }
    }

    private func closeLog() {
        try? logHandle?.close()
        logHandle = nil
    }
}
