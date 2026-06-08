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
        cancelHealthLoop()
        if isRunning || state == .starting {
            state = .stopping
            expectingExit = true
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
        }
        process = nil
        closeLog()
        // Always release :11400 on quit — including failed/stopped states where the
        // supervised child exited 0 but an orphan agent still holds the port.
        releaseListenPort()
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
        releaseListenPort()
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
            proc.arguments = ["serve", "-q", "--replace", "--config", configPath.path]
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
        if code == 0 {
            Task { @MainActor [weak self] in
                guard let self else { return }
                if await self.isPortHealthy() {
                    let pid = self.portOwnerPid() ?? 0
                    self.autoRestartCount = 0
                    self.consecutiveFailures = 0
                    self.lastHealthyAt = Date()
                    self.update(.running(pid: pid > 0 ? pid : 0))
                    self.startHealthCheckLoop()
                    return
                }
                self.tryAutoRestart(reason: "Agent exited with code \(code)")
            }
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
            // Only mark running when our child process is alive. Another netllm
            // instance on the same port would also pass /health and cause a
            // false "running" state (e.g. stale ./netllm serve in a terminal).
            if healthy, let proc = process, proc.isRunning {
                consecutiveFailures = 0
                lastHealthyAt = Date()
                update(.running(pid: proc.processIdentifier))
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

    /// Stop any netllm agent still bound to our listen port (orphan after app quit).
    private func releaseListenPort() {
        let script = """
        from netllm_discovery.runtime import stop_netllm_on_port
        stop_netllm_on_port(\(port))
        """
        let proc = Process()
        proc.executableURL = runtime.executable
        proc.arguments = ["-c", script]
        proc.environment = runtime.makeEnvironment()
        proc.standardOutput = FileHandle.nullDevice
        proc.standardError = FileHandle.nullDevice
        try? proc.run()
        proc.waitUntilExit()
    }

    @MainActor
    private func isPortHealthy() async -> Bool {
        let url = URL(string: "http://\(host):\(port)/health")!
        var request = URLRequest(url: url, timeoutInterval: 2)
        request.httpMethod = "GET"
        do {
            let (_, response) = try await URLSession.shared.data(for: request)
            return (response as? HTTPURLResponse)?.statusCode == 200
        } catch {
            return false
        }
    }

    private func portOwnerPid() -> Int32? {
        let script = """
        from netllm_discovery.process_util import port_owner_pid
        pid = port_owner_pid(\(port))
        print(pid if pid is not None else 0)
        """
        let proc = Process()
        let pipe = Pipe()
        proc.executableURL = runtime.executable
        proc.arguments = ["-c", script]
        proc.environment = runtime.makeEnvironment()
        proc.standardOutput = pipe
        proc.standardError = FileHandle.nullDevice
        guard (try? proc.run()) != nil else { return nil }
        proc.waitUntilExit()
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        guard let text = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines),
              let pid = Int32(text), pid > 0
        else { return nil }
        return pid
    }
}
