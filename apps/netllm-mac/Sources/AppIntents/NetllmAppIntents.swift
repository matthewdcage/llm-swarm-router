import AppIntents
import AppKit
import Foundation

// MARK: - Entities

struct ModelEntity: AppEntity {
    static let typeDisplayRepresentation = TypeDisplayRepresentation(name: "Model")
    static let defaultQuery = ModelEntityQuery()

    var id: String

    var displayRepresentation: DisplayRepresentation {
        DisplayRepresentation(title: "\(id)")
    }
}

struct ModelEntityQuery: EntityQuery {
    func entities(for identifiers: [ModelEntity.ID]) async throws -> [ModelEntity] {
        identifiers.map { ModelEntity(id: $0) }
    }

    func suggestedEntities() async throws -> [ModelEntity] {
        let (_, _, baseURL) = ClientEnvExporter.endpointsFromConfig()
        let models = await AgentAPI.models(baseURL: baseURL)
        return models.map { ModelEntity(id: $0.model) }
    }
}

struct BackendEntity: AppEntity {
    static let typeDisplayRepresentation = TypeDisplayRepresentation(name: "Backend")
    static let defaultQuery = BackendEntityQuery()

    var id: String
    var provider: String
    var health: String

    var displayRepresentation: DisplayRepresentation {
        DisplayRepresentation(title: "\(provider)", subtitle: "\(health)")
    }
}

struct BackendEntityQuery: EntityQuery {
    func entities(for identifiers: [BackendEntity.ID]) async throws -> [BackendEntity] {
        let (_, _, baseURL) = ClientEnvExporter.endpointsFromConfig()
        guard let status = await AgentAPI.status(baseURL: baseURL) else { return [] }
        return status.backends
            .filter { identifiers.contains($0.baseURL) }
            .map {
                BackendEntity(id: $0.baseURL, provider: $0.provider, health: $0.health)
            }
    }

    func suggestedEntities() async throws -> [BackendEntity] {
        let (_, _, baseURL) = ClientEnvExporter.endpointsFromConfig()
        guard let status = await AgentAPI.status(baseURL: baseURL) else { return [] }
        return status.backends.map {
            BackendEntity(id: $0.baseURL, provider: $0.provider, health: $0.health)
        }
    }
}

struct PeerEntity: AppEntity {
    static let typeDisplayRepresentation = TypeDisplayRepresentation(name: "Peer")
    static let defaultQuery = PeerEntityQuery()

    var id: String
    var hostname: String

    var displayRepresentation: DisplayRepresentation {
        DisplayRepresentation(title: "\(hostname)", subtitle: "\(id)")
    }
}

struct PeerEntityQuery: EntityQuery {
    func entities(for identifiers: [PeerEntity.ID]) async throws -> [PeerEntity] {
        let (_, _, baseURL) = ClientEnvExporter.endpointsFromConfig()
        guard let status = await AgentAPI.status(baseURL: baseURL) else { return [] }
        return status.peers
            .filter { identifiers.contains($0.agentId) }
            .map { PeerEntity(id: $0.agentId, hostname: $0.hostname) }
    }

    func suggestedEntities() async throws -> [PeerEntity] {
        let (_, _, baseURL) = ClientEnvExporter.endpointsFromConfig()
        guard let status = await AgentAPI.status(baseURL: baseURL) else { return [] }
        return status.peers.map { PeerEntity(id: $0.agentId, hostname: $0.hostname) }
    }
}

// MARK: - Intents

struct CopyClientEnvIntent: AppIntent {
    static let title: LocalizedStringResource = "Copy netllm Client Environment"
    static let description = IntentDescription(
        "Copy OPENAI_BASE_URL and ANTHROPIC_BASE_URL exports for local mesh clients."
    )

    func perform() async throws -> some IntentResult & ProvidesDialog {
        let (host, port, _) = ClientEnvExporter.endpointsFromConfig()
        ClientEnvExporter.copyToPasteboard(host: host, port: port)
        return .result(dialog: "Copied client environment for http://\(host):\(port)")
    }
}

struct OpenDashboardIntent: AppIntent {
    static let title: LocalizedStringResource = "Open netllm Dashboard"
    static let description = IntentDescription("Open the netllm web dashboard in your browser.")
    static let openAppWhenRun: Bool = true

    func perform() async throws -> some IntentResult {
        let (host, port, _) = ClientEnvExporter.endpointsFromConfig()
        guard let url = URL(string: "http://\(host):\(port)/ui/") else {
            throw IntentError.message("Invalid dashboard URL")
        }
        _ = await MainActor.run { NSWorkspace.shared.open(url) }
        return .result()
    }
}

struct GetAgentStatusIntent: AppIntent {
    static let title: LocalizedStringResource = "Get netllm Agent Status"
    static let description = IntentDescription("Summarize agent role, backends, and swarm peers.")

    func perform() async throws -> some IntentResult & ProvidesDialog {
        let (_, _, baseURL) = ClientEnvExporter.endpointsFromConfig()
        guard let status = await AgentAPI.status(baseURL: baseURL) else {
            throw IntentError.message("Agent is not reachable at \(baseURL.absoluteString)")
        }
        let online = status.backends.filter { $0.health == "online" }.count
        let summary = """
        Role: \(status.role)
        Listen: \(status.listenURL)
        Backends: \(online)/\(status.backends.count) online
        Peers: \(status.peers.count)
        """
        return .result(dialog: IntentDialog(stringLiteral: summary))
    }
}

struct ListModelsIntent: AppIntent {
    static let title: LocalizedStringResource = "List netllm Models"
    static let description = IntentDescription("List models routed by the local netllm agent.")

    func perform() async throws -> some IntentResult & ReturnsValue<[ModelEntity]> {
        let (_, _, baseURL) = ClientEnvExporter.endpointsFromConfig()
        let models = await AgentAPI.models(baseURL: baseURL)
        return .result(value: models.map { ModelEntity(id: $0.model) })
    }
}

struct StartAgentIntent: AppIntent {
    static let title: LocalizedStringResource = "Start netllm Agent"
    static let description = IntentDescription("Start the netllm agent subprocess.")
    static let openAppWhenRun: Bool = true

    func perform() async throws -> some IntentResult & ProvidesDialog {
        guard let response = await AppControlClient.send(command: .start) else {
            throw IntentError.message(
                "Could not reach netllm control socket. Is the menubar app running?"
            )
        }
        guard response.ok else {
            throw IntentError.message(response.message ?? "Start failed (\(response.status))")
        }
        return .result(dialog: "Agent \(response.state) on \(response.host):\(response.port)")
    }
}

// MARK: - Shortcuts

struct NetllmShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(
            intent: CopyClientEnvIntent(),
            phrases: [
                "Copy netllm client env with \(.applicationName)",
                "Copy netllm environment with \(.applicationName)",
            ],
            shortTitle: "Copy Client Env",
            systemImageName: "doc.on.clipboard"
        )
        AppShortcut(
            intent: ListModelsIntent(),
            phrases: [
                "List netllm models with \(.applicationName)",
                "Show netllm models with \(.applicationName)",
            ],
            shortTitle: "List Models",
            systemImageName: "cube.box"
        )
        AppShortcut(
            intent: GetAgentStatusIntent(),
            phrases: [
                "Get netllm status with \(.applicationName)",
                "Check netllm agent with \(.applicationName)",
            ],
            shortTitle: "Agent Status",
            systemImageName: "gauge.with.dots.needle.67percent"
        )
        AppShortcut(
            intent: OpenDashboardIntent(),
            phrases: [
                "Open netllm dashboard with \(.applicationName)",
            ],
            shortTitle: "Open Dashboard",
            systemImageName: "safari"
        )
        AppShortcut(
            intent: StartAgentIntent(),
            phrases: [
                "Start netllm agent with \(.applicationName)",
            ],
            shortTitle: "Start Agent",
            systemImageName: "play.fill"
        )
    }
}

private enum IntentError: Error, CustomLocalizedStringResourceConvertible {
    case message(String)

    var localizedStringResource: LocalizedStringResource {
        switch self {
        case .message(let text):
            LocalizedStringResource(stringLiteral: text)
        }
    }
}
