import Foundation

enum LogPaths {
    static func resolvedLogDir(logDirOverride: String) -> URL {
        let trimmed = logDirOverride.trimmingCharacters(in: .whitespacesAndNewlines)
        if !trimmed.isEmpty {
            let expanded = (trimmed as NSString).expandingTildeInPath
            return URL(fileURLWithPath: expanded, isDirectory: true)
        }
        return AppConfig.appSupportURL().appendingPathComponent("logs", isDirectory: true)
    }

    static func agentLogFile(logDirOverride: String) -> URL {
        resolvedLogDir(logDirOverride: logDirOverride).appendingPathComponent("agent.log")
    }

    static func logDirFromConfigFile() -> URL {
        let path = AppConfig.defaultConfigPath()
        guard FileManager.default.fileExists(atPath: path.path),
              let text = try? String(contentsOf: path, encoding: .utf8)
        else {
            return resolvedLogDir(logDirOverride: "")
        }
        var inUiSection = false
        for line in text.split(separator: "\n") {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if trimmed == "[ui]" {
                inUiSection = true
                continue
            }
            if trimmed.hasPrefix("[") {
                inUiSection = false
                continue
            }
            if inUiSection, trimmed.hasPrefix("log_dir") {
                if let value = parseTomlString(trimmed) {
                    return resolvedLogDir(logDirOverride: value)
                }
            }
        }
        return resolvedLogDir(logDirOverride: "")
    }

    private static func parseTomlString(_ line: String) -> String? {
        guard let eq = line.firstIndex(of: "=") else { return nil }
        var value = String(line[line.index(after: eq)...]).trimmingCharacters(in: .whitespaces)
        if value.hasPrefix("\""), value.hasSuffix("\""), value.count >= 2 {
            value = String(value.dropFirst().dropLast())
        }
        return value.isEmpty ? nil : value
    }
}

struct AgentLogsPayload: Sendable {
    var logDir: String
    var logFile: String
    var exists: Bool
    var sizeBytes: Int
    var tail: [String]
    var truncated: Bool
}
