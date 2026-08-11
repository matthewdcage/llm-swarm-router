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

/// One row of `/netllm/v1/logs` `records[]` (UI-11) — the same window the
/// payload also returns as raw `tail[]`, parsed by the agent so every client
/// does not re-derive the formatter's shape.
///
/// `raw` is always the original line; everything else is best-effort. A line
/// the formatter did not produce (a traceback continuation, a bare print)
/// comes back with `level`/`ts`/`logger` null and the whole line as `message`
/// — `admin.parse_log_line` never drops a line, so a window is never lossy.
struct AgentLogRecord: Sendable, Identifiable {
    /// 1-based line number within agent.log — unique inside a window, and
    /// the same units as the `before` paging cursor.
    var id: Int { lineNo }
    var lineNo: Int
    var ts: String?
    /// Normalized to one of `error` / `warn` / `info` / `debug`, or nil when
    /// the line carried no level this app should colour on.
    var level: String?
    /// The level exactly as the line spelled it (`WARNING`, `warn`, …).
    var levelLabel: String?
    var logger: String?
    var message: String
    var raw: String
}

struct AgentLogsPayload: Sendable {
    var logDir: String
    var logFile: String
    var exists: Bool
    var sizeBytes: Int
    var tail: [String]
    var truncated: Bool
    /// UI-11 additions, all absent on an older agent — which is exactly why
    /// `tail` stays the rendering fallback rather than being replaced.
    var records: [AgentLogRecord] = []
    /// Lines in the whole file, not in this window.
    var totalLines: Int = 0
    /// 1-based cursor for the next older page; nil at the start of the file.
    var nextBefore: Int?
    /// The same route with `download=1`. Named by the payload so no client
    /// builds the URL, and so the button hides on an agent without it.
    /// The file is unredacted — everything the agent logged, secrets included.
    var downloadURL: String?
}
