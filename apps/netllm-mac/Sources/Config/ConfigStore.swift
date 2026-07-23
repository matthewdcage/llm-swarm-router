import Foundation

@MainActor
final class ConfigStore {
    private let cli: CLIRunner

    init(runtime: PythonRuntime, configPath: URL = AppConfig.defaultConfigPath()) {
        cli = CLIRunner(runtime: runtime, configPath: configPath)
    }

    func load() throws -> NetllmConfigDocument {
        let raw = try cli.run(["config", "export"])
        let data = Data(raw.utf8)
        var document = try JSONDecoder().decode(NetllmConfigDocument.self, from: data)
        document.routing.sources = document.routing.sources.map(Self.blankSourceSecret)
        return document
    }

    /// `netllm config export` is a general-purpose CLI round-trip (also
    /// used for backup/restore), so it intentionally returns the real
    /// `routing.sources[].secret` value unredacted — unlike the web
    /// dashboard's `GET /netllm/v1/config`, which blanks it in
    /// `admin._source_export` before it ever leaves the process. Blank it
    /// here instead, right after decode, so the Settings UI's SecureField
    /// never holds/displays the real secret in memory — matching the
    /// dashboard's guarantee without changing the CLI's own export
    /// contract. Safe on Save: `apply_config_patch`'s source merge
    /// (packages/netllm-core/src/netllm_core/config_merge.py) already
    /// preserves the stored secret whenever the incoming value is empty.
    private static func blankSourceSecret(_ source: JSONValue) -> JSONValue {
        guard var object = source.objectValue, object["secret"] != nil else { return source }
        object["secret"] = .string("")
        return .object(object)
    }

    /// Form shape for the schema-driven config sections (currently `ui`
    /// only — see docs/config-schema-rewrite-plan.md §5 phase 4). Reached
    /// via the bundled CLI, not HTTP, so it works even when the agent
    /// process isn't running (same reasoning as `load()`/`save()` above).
    func loadSchema() throws -> ConfigSchema {
        let raw = try cli.run(["config", "schema"])
        let data = Data(raw.utf8)
        return try JSONDecoder().decode(ConfigSchema.self, from: data)
    }

    func save(_ document: NetllmConfigDocument) throws -> URL {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        let json = String(data: try encoder.encode(document), encoding: .utf8) ?? "{}"
        let raw = try cli.run(["config", "import"], stdin: json)
        if let data = raw.data(using: .utf8),
           let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let path = obj["path"] as? String {
            return URL(fileURLWithPath: path)
        }
        return AppConfig.defaultConfigPath()
    }
}
