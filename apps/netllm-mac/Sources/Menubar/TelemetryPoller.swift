import Foundation

extension Notification.Name {
    static let menubarTelemetryDidUpdate = Notification.Name("MenubarTelemetryDidUpdate")
}

struct TelemetrySnapshot {
    var raw: [String: Any] = [:]

    var omlxAvailable: Bool {
        (raw["omlx"] as? [String: Any])?["available"] as? Bool ?? false
    }

    var routerSession: [String: Any] {
        if omlxAvailable, let session = (raw["omlx"] as? [String: Any])?["session"] as? [String: Any] {
            return session
        }
        return (raw["router"] as? [String: Any])?["session"] as? [String: Any] ?? [:]
    }

    var routerAlltime: [String: Any] {
        if omlxAvailable, let alltime = (raw["omlx"] as? [String: Any])?["alltime"] as? [String: Any] {
            return alltime
        }
        return (raw["router"] as? [String: Any])?["alltime"] as? [String: Any] ?? [:]
    }

    var livePP: Double {
        let live = (raw["omlx"] as? [String: Any])?["live"] as? [String: Any]
        return (live?["prefill_tps"] as? NSNumber)?.doubleValue ?? 0
    }

    var liveTG: Double {
        let live = (raw["omlx"] as? [String: Any])?["live"] as? [String: Any]
        return (live?["generation_tps"] as? NSNumber)?.doubleValue ?? 0
    }

    var loadedModels: [String] {
        (raw["omlx"] as? [String: Any])?["loaded_models"] as? [String] ?? []
    }

    var primaryModel: String? {
        (raw["omlx"] as? [String: Any])?["primary_model"] as? String
    }

    var modelMemoryUsed: Int {
        Int((raw["omlx"] as? [String: Any])?["model_memory_used"] as? NSNumber ?? 0)
    }
}

@MainActor
final class TelemetryPoller {
    static let didUpdateNotification = Notification.Name.menubarTelemetryDidUpdate

    private let baseURL: URL
    private var task: Task<Void, Never>?
    private(set) var snapshot = TelemetrySnapshot()

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
        guard let json = await fetchJSON(path: "/netllm/v1/telemetry?watch=1&history=60") else { return }
        snapshot = TelemetrySnapshot(raw: json)
        NotificationCenter.default.post(name: Self.didUpdateNotification, object: self)
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
