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
    private static let portQueue = DispatchQueue(label: "com.netllm.port", qos: .utility)

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
        case .failed, .stopped:
            autoRestartCount = 0
            consecutiveFailures = 0
        default:
            break
        }
        if portHealthySync() {
            Task { @MainActor in
                self.adoptHealthyListener()
            }
            return .alreadyRunning
        }
        // Claim .starting before slow port work so concurrent start() calls cannot spawn twins.
        update(.starting)
        if portHealthySync() {
            releaseListenPort()
        }
        try doStart()
        return .started
    }

    /// Keep polling /health so orphan agents and failed supervisor states can self-heal.
    func beginPortMonitoring() {
        startHealthCheckLoop()
    }

    /// On launch (or after a failed supervisor state), adopt a healthy agent already on our port.
    @MainActor
    func reconcileListeningPort(adoptOrphan: Bool) async {
        guard adoptOrphan else { return }
        switch state {
        case .stopped, .failed:
            break
        default:
            return
        }
        guard await isPortHealthy() else { return }
        adoptHealthyListener()
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
        if let proc = process, proc.isRunning {
            return
        }
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

        if case .starting = state {
            // start() / tryAutoRestart already claimed this state.
        } else {
            update(.starting)
        }
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
                for attempt in 0..<4 {
                    if await self.isPortHealthy() {
                        self.adoptHealthyListener()
                        return
                    }
                    if attempt < 3 {
                        try? await Task.sleep(for: .milliseconds(250))
                    }
                }
                if await self.isPortHealthy() {
                    self.releaseListenPort()
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
            beginPortMonitoring()
            return
        }
        autoRestartCount += 1
        if portHealthySync() {
            releaseListenPort()
        }
        let backoff = TimeInterval(5 * (1 << (autoRestartCount - 1)))
        update(.starting)
        Task { @MainActor [weak self] in
            try? await Task.sleep(for: .seconds(backoff))
            guard let self, case .starting = self.state else { return }
            if self.portHealthySync() {
                self.releaseListenPort()
            }
            try? self.doStart()
        }
    }

    private func startHealthCheckLoop() {
        cancelHealthLoop()
        healthTask = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                await self.tickHealth()
                let interval: TimeInterval
                switch self.state {
                case .starting:
                    interval = 1
                default:
                    interval = self.healthCheckInterval
                }
                try? await Task.sleep(for: .seconds(interval))
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
                let pid: Int32
                if let proc = process, proc.isRunning {
                    pid = proc.processIdentifier
                } else {
                    pid = portOwnerPid() ?? 0
                }
                update(.running(pid: pid > 0 ? pid : 0))
            }
        case .failed:
            if healthy {
                adoptHealthyListener()
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

    @MainActor
    private func adoptHealthyListener() {
        let pid = portOwnerPid() ?? 0
        autoRestartCount = 0
        consecutiveFailures = 0
        lastHealthyAt = Date()
        update(.running(pid: pid > 0 ? pid : 0))
        startHealthCheckLoop()
    }

    /// Stop any netllm agent still bound to our listen port (orphan after app quit).
    /// Runs off the main thread so AppControlServer handlers do not deadlock.
    private func releaseListenPort() {
        let script = """
        from netllm_discovery.runtime import stop_netllm_on_port
        stop_netllm_on_port(\(port))
        """
        _ = runPythonScript(script, captureStdout: false)
    }

    private func portHealthySync() -> Bool {
        let url = URL(string: "http://\(host):\(port)/health")!
        var request = URLRequest(url: url, timeoutInterval: 2)
        request.httpMethod = "GET"
        let sem = DispatchSemaphore(value: 0)
        var ok = false
        let task = URLSession.shared.dataTask(with: request) { _, response, _ in
            ok = (response as? HTTPURLResponse)?.statusCode == 200
            sem.signal()
        }
        task.resume()
        if Thread.isMainThread {
            while sem.wait(timeout: .now()) != .success {
                RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.05))
            }
        } else {
            sem.wait()
        }
        return ok
    }

    private func runPythonScript(
        _ script: String,
        captureStdout: Bool
    ) -> (exitCode: Int32, stdout: String?) {
        if Thread.isMainThread {
            var result: (exitCode: Int32, stdout: String?) = (1, nil)
            let group = DispatchGroup()
            group.enter()
            Self.portQueue.async { [runtime] in
                result = Self.executePythonScript(
                    script,
                    captureStdout: captureStdout,
                    runtime: runtime
                )
                group.leave()
            }
            while group.wait(timeout: .now()) != .success {
                RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.05))
            }
            return result
        }
        return Self.executePythonScript(script, captureStdout: captureStdout, runtime: runtime)
    }

    private static func executePythonScript(
        _ script: String,
        captureStdout: Bool,
        runtime: PythonRuntime
    ) -> (exitCode: Int32, stdout: String?) {
        let proc = Process()
        proc.executableURL = runtime.executable
        proc.arguments = ["-c", script]
        proc.environment = runtime.makeEnvironment()
        if captureStdout {
            let pipe = Pipe()
            proc.standardOutput = pipe
            proc.standardError = FileHandle.nullDevice
            guard (try? proc.run()) != nil else { return (1, nil) }
            proc.waitUntilExit()
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            let stdout = String(data: data, encoding: .utf8)?
                .trimmingCharacters(in: .whitespacesAndNewlines)
            return (proc.terminationStatus, stdout)
        }
        proc.standardOutput = FileHandle.nullDevice
        proc.standardError = FileHandle.nullDevice
        try? proc.run()
        proc.waitUntilExit()
        return (proc.terminationStatus, nil)
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
        let result = runPythonScript(script, captureStdout: true)
        guard let text = result.stdout,
              let pid = Int32(text), pid > 0
        else { return nil }
        return pid
    }
}
