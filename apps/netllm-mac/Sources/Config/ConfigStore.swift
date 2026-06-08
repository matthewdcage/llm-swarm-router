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
        return try JSONDecoder().decode(NetllmConfigDocument.self, from: data)
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
