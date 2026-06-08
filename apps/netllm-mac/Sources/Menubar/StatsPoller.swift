import Foundation

struct BackendSnapshot: Sendable, Identifiable {
    var id: String { baseURL }
    var provider: String
    var baseURL: String
    var health: String
    var modelCount: Int
}

struct StatsSnapshot: Sendable {
    var backendCount: Int = 0
    var peerCount: Int = 0
    var role: String = "peer"
    var modelsPreview: String = ""
    var backends: [BackendSnapshot] = []
    var onlineBackendCount: Int = 0
}

@MainActor
final class StatsPoller {
    private let baseURL: URL
    private var task: Task<Void, Never>?
    private(set) var snapshot = StatsSnapshot()
    var onUpdate: (() -> Void)?

    init(host: String, port: Int) {
        baseURL = URL(string: "http://\(host):\(port)")!
    }

    func start() {
        stop()
        task = Task { [weak self] in
            while !Task.isCancelled {
                await self?.poll()
                try? await Task.sleep(for: .seconds(1))
            }
        }
    }

    func stop() {
        task?.cancel()
        task = nil
    }

    private func poll() async {
        var snap = StatsSnapshot()
        if let status = await fetchJSON(path: "/netllm/v1/status") {
            snap.role = status["role"] as? String ?? "peer"
            if let backends = status["backends"] as? [[String: Any]] {
                snap.backends = backends.map { row in
                    let health = row["health"] as? [String: Any] ?? [:]
                    return BackendSnapshot(
                        provider: row["provider"] as? String ?? "?",
                        baseURL: row["base_url"] as? String ?? "",
                        health: health["status"] as? String ?? "unknown",
                        modelCount: health["model_count"] as? Int ?? 0
                    )
                }
                snap.backendCount = snap.backends.count
                snap.onlineBackendCount = snap.backends.filter { $0.health == "online" }.count
            }
            if let peers = status["peers"] as? [[String: Any]] {
                snap.peerCount = peers.count
            }
        }
        if let models = await fetchJSON(path: "/v1/models") {
            if let data = models["data"] as? [[String: Any]] {
                let names = data.prefix(3).compactMap { $0["id"] as? String }
                snap.modelsPreview = names.joined(separator: ", ")
            }
        }
        snapshot = snap
        onUpdate?()
    }

    private func fetchJSON(path: String) async -> [String: Any]? {
        var request = URLRequest(url: baseURL.appendingPathComponent(path))
        request.timeoutInterval = 2
        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard (response as? HTTPURLResponse)?.statusCode == 200 else { return nil }
            return try JSONSerialization.jsonObject(with: data) as? [String: Any]
        } catch {
            return nil
        }
    }
}
