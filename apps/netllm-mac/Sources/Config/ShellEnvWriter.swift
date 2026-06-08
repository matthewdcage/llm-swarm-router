import Foundation

enum ShellEnvWriter {
    static func shimPath() -> URL {
        AppConfig.defaultConfigPath()
            .deletingLastPathComponent()
            .appendingPathComponent("bin/netllm")
    }

    static func ensureCLIShim(bundleCLI: URL) {
        let shimPath = shimPath()
        let shimDir = shimPath.deletingLastPathComponent()
        try? FileManager.default.createDirectory(at: shimDir, withIntermediateDirectories: true)

        let script = """
        #!/bin/sh
        # netllm CLI shim — installed by netllm-mac.app
        exec '\(bundleCLI.path)' "$@"
        """
        try? script.write(to: shimPath, atomically: true, encoding: .utf8)
        try? FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: shimPath.path)
    }
}
