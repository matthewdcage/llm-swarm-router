import AppKit
import Foundation

enum ClientEnvExporter {
    static func exportScript(host: String, port: Int) -> String {
        """
        export OPENAI_BASE_URL=http://\(host):\(port)/v1
        export OPENAI_API_KEY=netllm-local
        export ANTHROPIC_BASE_URL=http://\(host):\(port)
        export ANTHROPIC_API_KEY=netllm-local
        """
    }

    static func copyToPasteboard(host: String, port: Int) {
        let text = exportScript(host: host, port: port)
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
    }

    static func endpointsFromConfig() -> (host: String, port: Int, baseURL: URL) {
        let config = AppConfig.load()
        let host = AppConfig.connectableHost(for: config.bindHost)
        let baseURL = URL(string: "http://\(host):\(config.port)")!
        return (host, config.port, baseURL)
    }
}
