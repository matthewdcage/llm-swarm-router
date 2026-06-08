import Foundation

struct AppConfig: Sendable {
    var configPath: URL
    var bindHost: String
    var port: Int
    var autoStartOnLaunch: Bool
    var checkForUpdatesAutomatically: Bool
    var role: String
    var advertise: Bool
    var mdns: Bool
    var needsWelcome: Bool

    static func appSupportURL() -> URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/netllm", isDirectory: true)
    }

    static func defaultConfigPath() -> URL {
        let xdg = ProcessInfo.processInfo.environment["XDG_CONFIG_HOME"]
        if let xdg, !xdg.isEmpty {
            return URL(fileURLWithPath: xdg).appendingPathComponent("netllm/config.toml")
        }
        return FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".config/netllm/config.toml")
    }

    static func connectableHost(for bind: String) -> String {
        bind == "0.0.0.0" ? "127.0.0.1" : bind
    }

    static func load() -> AppConfig {
        let path = defaultConfigPath()
        let exists = FileManager.default.fileExists(atPath: path.path)
        var host = "127.0.0.1"
        var port = 11400
        var autoStart = true
        var checkUpdates = true
        var role = "peer"
        var advertise = true
        var mdns = true

        if exists, let text = try? String(contentsOf: path, encoding: .utf8) {
            for line in text.split(separator: "\n") {
                let trimmed = line.trimmingCharacters(in: .whitespaces)
                if trimmed.hasPrefix("listen") {
                    if let value = parseTomlString(trimmed) {
                        let parts = value.split(separator: ":")
                        if let h = parts.first { host = String(h) }
                        if parts.count > 1, let p = Int(parts[1]) { port = p }
                    }
                } else if trimmed.hasPrefix("auto_start_on_launch") {
                    if let v = parseTomlBool(trimmed) { autoStart = v }
                } else if trimmed.hasPrefix("check_for_updates_automatically") {
                    if let v = parseTomlBool(trimmed) { checkUpdates = v }
                } else if trimmed.hasPrefix("role") && trimmed.contains("=") {
                    if let v = parseTomlString(trimmed) { role = v }
                } else if trimmed.hasPrefix("advertise") {
                    if let v = parseTomlBool(trimmed) { advertise = v }
                } else if trimmed.hasPrefix("mdns") {
                    if let v = parseTomlBool(trimmed) { mdns = v }
                }
            }
        }

        return AppConfig(
            configPath: path,
            bindHost: host,
            port: port,
            autoStartOnLaunch: autoStart,
            checkForUpdatesAutomatically: checkUpdates,
            role: role,
            advertise: advertise,
            mdns: mdns,
            needsWelcome: !exists
        )
    }

    func save(bindHost: String, port: Int, autoStart: Bool, lanMode: Bool) throws {
        let path = configPath
        try FileManager.default.createDirectory(at: path.deletingLastPathComponent(), withIntermediateDirectories: true)

        var lines: [String] = []
        if FileManager.default.fileExists(atPath: path.path),
           let existing = try? String(contentsOf: path, encoding: .utf8) {
            lines = existing.split(separator: "\n", omittingEmptySubsequences: false).map(String.init)
        } else {
            lines = [
                "[agent]",
                "listen = \"127.0.0.1:11400\"",
                "role = \"peer\"",
                "advertise = true",
                "",
                "[discovery]",
                "providers = [\"omlx\", \"ollama\", \"lmstudio\"]",
                "",
                "[swarm]",
                "mdns = true",
                "",
                "[routing]",
                "default_strategy = \"local_first\"",
                "",
                "[ui]",
                "auto_start_on_launch = true",
                "check_for_updates_automatically = true",
            ]
        }

        let listen = lanMode ? "0.0.0.0:\(port)" : "\(bindHost):\(port)"
        lines = mergeTomlValue(lines, key: "listen", value: "\"\(listen)\"", section: "[agent]")
        lines = mergeTomlValue(lines, key: "auto_start_on_launch", value: autoStart ? "true" : "false", section: "[ui]")
        try lines.joined(separator: "\n").write(to: path, atomically: true, encoding: .utf8)
    }

    private static func parseTomlString(_ line: String) -> String? {
        guard let eq = line.firstIndex(of: "=") else { return nil }
        var value = String(line[line.index(after: eq)...]).trimmingCharacters(in: .whitespaces)
        if value.hasPrefix("\"") && value.hasSuffix("\"") {
            value = String(value.dropFirst().dropLast())
        }
        return value.isEmpty ? nil : value
    }

    private static func parseTomlBool(_ line: String) -> Bool? {
        guard let eq = line.firstIndex(of: "=") else { return nil }
        let value = String(line[line.index(after: eq)...]).trimmingCharacters(in: .whitespaces)
        return value == "true"
    }

    private func mergeTomlValue(_ lines: [String], key: String, value: String, section: String) -> [String] {
        var out = lines
        var inSection = false
        var replaced = false
        for (i, line) in out.enumerated() {
            if line.trimmingCharacters(in: .whitespaces) == section {
                inSection = true
                continue
            }
            if inSection && line.hasPrefix("[") { inSection = false }
            if inSection && line.trimmingCharacters(in: .whitespaces).hasPrefix(key) {
                out[i] = "\(key) = \(value)"
                replaced = true
                break
            }
        }
        if !replaced {
            if !out.contains(section) { out.append(section) }
            out.append("\(key) = \(value)")
        }
        return out
    }
}
