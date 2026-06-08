import Foundation

struct CLIRunner: Sendable {
    let cliPath: URL
    let configPath: URL
    let environment: [String: String]

    init(runtime: PythonRuntime, configPath: URL) {
        self.configPath = configPath
        self.environment = runtime.makeEnvironment()
        self.cliPath = CLIRunner.resolveCLIPath(runtime: runtime)
    }

    /// Bundled netllm-cli wrapper, else PATH shim / global netllm.
    static func resolveCLIPath(runtime: PythonRuntime) -> URL {
        let bundled = runtime.bundleCLIPath
        if FileManager.default.isExecutableFile(atPath: bundled.path) {
            return bundled
        }
        let candidates = [
            ShellEnvWriter.shimPath(),
            URL(fileURLWithPath: "/opt/homebrew/bin/netllm"),
            URL(fileURLWithPath: "/usr/local/bin/netllm"),
        ]
        for url in candidates where FileManager.default.isExecutableFile(atPath: url.path) {
            return url
        }
        return bundled
    }

    func run(_ arguments: [String], stdin: String? = nil) throws -> String {
        guard FileManager.default.isExecutableFile(atPath: cliPath.path) else {
            throw CLIError.missingExecutable(cliPath.path)
        }

        let process = Process()
        process.executableURL = cliPath
        process.arguments = arguments + ["--config", configPath.path]
        process.environment = environment

        let outPipe = Pipe()
        let errPipe = Pipe()
        process.standardOutput = outPipe
        process.standardError = errPipe
        if let stdin {
            let inPipe = Pipe()
            process.standardInput = inPipe
            try process.run()
            if let data = stdin.data(using: .utf8) {
                inPipe.fileHandleForWriting.write(data)
            }
            inPipe.fileHandleForWriting.closeFile()
        } else {
            try process.run()
        }
        process.waitUntilExit()

        let stdout = String(data: outPipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
        let stderr = String(data: errPipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
        guard process.terminationStatus == 0 else {
            throw CLIError.failed(command: arguments.joined(separator: " "), stderr: stderr, stdout: stdout)
        }
        return stdout
    }

    enum CLIError: Error, LocalizedError {
        case missingExecutable(String)
        case failed(command: String, stderr: String, stdout: String)

        var errorDescription: String? {
            switch self {
            case .missingExecutable(let path):
                return "\(AppBranding.cliCommand) CLI not found at \(path). Reinstall \(AppBranding.displayName) from build/Stage/netllm-mac.app or run ./\(AppBranding.cliCommand) install."
            case .failed(let command, let stderr, _):
                return stderr.isEmpty ? "Command failed: \(command)" : stderr.trimmingCharacters(in: .whitespacesAndNewlines)
            }
        }
    }
}
