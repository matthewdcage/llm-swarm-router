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
        (raw["router"] as? [String: Any])?["session"] as? [String: Any] ?? [:]
    }

    var routerAlltime: [String: Any] {
        (raw["router"] as? [String: Any])?["alltime"] as? [String: Any] ?? [:]
    }

    var omlxSession: [String: Any] {
        (raw["omlx"] as? [String: Any])?["session"] as? [String: Any] ?? [:]
    }

    var omlxAlltime: [String: Any] {
        (raw["omlx"] as? [String: Any])?["alltime"] as? [String: Any] ?? [:]
    }

    var routedRequests: [String: Int] {
        let rawMap = (raw["router"] as? [String: Any])?["routed_requests"] as? [String: Any] ?? [:]
        var out: [String: Int] = [:]
        for (key, value) in rawMap {
            if let n = value as? Int {
                out[key] = n
            } else if let n = value as? NSNumber {
                out[key] = n.intValue
            }
        }
        return out
    }

    var routerInFlight: Int {
        Int(truncating: (raw["router"] as? [String: Any])?["in_flight_total"] as? NSNumber ?? 0)
    }

    /// Legacy: oMLX session when admin is up, else router session (prefer explicit router* above).
    var displaySessionScope: [String: Any] {
        if omlxAvailable, !omlxSession.isEmpty { return omlxSession }
        return routerSession
    }

    var displayAlltimeScope: [String: Any] {
        if omlxAvailable, !omlxAlltime.isEmpty { return omlxAlltime }
        return routerAlltime
    }

    /// `router.live` — measured over the agent's own rolling window. Present
    /// on every mesh, unlike `omlx.live` which needs an oMLX admin API.
    var routerLive: [String: Any] {
        (raw["router"] as? [String: Any])?["live"] as? [String: Any] ?? [:]
    }

    /// `router.latency` — TTFT percentiles. Values are null until a streaming
    /// request has been observed, so callers must keep nil distinct from 0.
    var routerLatency: [String: Any] {
        (raw["router"] as? [String: Any])?["latency"] as? [String: Any] ?? [:]
    }

    /// Live prefill throughput, preferring oMLX's reading and falling back to
    /// the router's own. Reported 0 on any non-oMLX mesh before the fallback
    /// existed, which read as "idle" rather than "not measured here".
    ///
    /// `nil` means neither source has measured one: `router.live.prefill_tps`
    /// is `null` until a streaming request has been seen, and `NSNull` must
    /// not fold to 0 — "idle" and "never measured" are different claims and
    /// the second is what produced the fabricated figures UI-2 removed.
    var livePrefillTps: Double? {
        let live = (raw["omlx"] as? [String: Any])?["live"] as? [String: Any]
        if let value = (live?["prefill_tps"] as? NSNumber)?.doubleValue, value > 0 {
            return value
        }
        return (routerLive["prefill_tps"] as? NSNumber)?.doubleValue
    }

    var liveGenerationTps: Double? {
        let live = (raw["omlx"] as? [String: Any])?["live"] as? [String: Any]
        if let value = (live?["generation_tps"] as? NSNumber)?.doubleValue, value > 0 {
            return value
        }
        return (routerLive["generation_tps"] as? NSNumber)?.doubleValue
    }

    /// Zero-coalesced convenience for callers that only compare against 0.
    /// Prefer the optional forms above anywhere the value is displayed.
    var livePP: Double { livePrefillTps ?? 0 }

    var liveTG: Double { liveGenerationTps ?? 0 }

    var loadedModels: [String] {
        (raw["omlx"] as? [String: Any])?["loaded_models"] as? [String] ?? []
    }

    var primaryModel: String? {
        (raw["omlx"] as? [String: Any])?["primary_model"] as? String
    }

    var modelMemoryUsed: Int {
        Int(truncating: (raw["omlx"] as? [String: Any])?["model_memory_used"] as? NSNumber ?? 0)
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
        guard let url = AgentHTTP.url(base: baseURL, path: path) else { return nil }
        var request = URLRequest(url: url)
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
