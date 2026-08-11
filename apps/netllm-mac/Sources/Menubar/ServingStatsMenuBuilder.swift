import AppKit

/// Status fields that live on `/netllm/v1/status` rather than telemetry.
struct ServingStatsStatusContext: Sendable {
    var sourceRequests: [String: Int] = [:]
    var scenarioRequests: [String: Int] = [:]
    var capacityRejections: Int = 0
    var shardlessFallbacks: Int = 0

    static let empty = ServingStatsStatusContext()
}

enum ServingStatsMenuBuilder {
    static func apply(
        to menu: NSMenu,
        snapshot: TelemetrySnapshot,
        status: ServingStatsStatusContext = .empty
    ) {
        menu.removeAllItems()

        if let primary = snapshot.primaryModel, !primary.isEmpty {
            addStat(menu, "Active model", primary)
            menu.addItem(.separator())
        } else if !snapshot.loadedModels.isEmpty {
            addStat(menu, "Loaded models", snapshot.loadedModels.joined(separator: ", "))
            menu.addItem(.separator())
        }

        appendLiveThroughput(menu, snapshot: snapshot)

        appendRouterSection(menu, title: "Router (session)", scope: snapshot.routerSession, includeRequests: true)
        menu.addItem(.separator())
        appendRouterSection(menu, title: "Router (all-time)", scope: snapshot.routerAlltime, includeRequests: true)

        let latency = snapshot.routerLatency
        if !latency.isEmpty {
            menu.addItem(.separator())
            let header = NSMenuItem(title: "Latency", action: nil, keyEquivalent: "")
            header.isEnabled = false
            menu.addItem(header)
            addStat(menu, "TTFT p50", formatOptionalMs(optionalDouble(latency["ttft_p50_ms"])))
            addStat(menu, "TTFT p95", formatOptionalMs(optionalDouble(latency["ttft_p95_ms"])))
            let samples = int(latency["ttft_samples"])
            if samples == 0 {
                addStat(menu, "TTFT samples", "none yet (streaming only)")
            } else {
                addStat(menu, "TTFT samples", CompactCountFormatter.format(samples), raw: samples)
            }
        }

        if snapshot.routerInFlight > 0 {
            menu.addItem(.separator())
            addStat(menu, "In-flight now", CompactCountFormatter.format(snapshot.routerInFlight))
        }

        appendCounterSections(menu, snapshot: snapshot, status: status)

        let routed = snapshot.routedRequests
        if !routed.isEmpty {
            menu.addItem(.separator())
            let header = NSMenuItem(title: "Routed by backend (all-time)", action: nil, keyEquivalent: "")
            header.isEnabled = false
            menu.addItem(header)
            for (key, count) in routed.sorted(by: { $0.value > $1.value }).prefix(8) {
                addStat(menu, key, CompactCountFormatter.format(count), raw: count)
            }
        }

        if let windowed = snapshot.windowedBackendCounts {
            menu.addItem(.separator())
            let header = NSMenuItem(
                title: "Routed by backend (\(windowed.spanLabel))",
                action: nil,
                keyEquivalent: ""
            )
            header.isEnabled = false
            menu.addItem(header)
            for (key, count) in windowed.counts.sorted(by: { $0.value > $1.value }).prefix(8) {
                addStat(menu, key, CompactCountFormatter.format(count), raw: count)
            }
        }

        if snapshot.omlxAvailable {
            menu.addItem(.separator())
            appendOmlxSection(menu, title: "oMLX (session)", scope: snapshot.omlxSession)
            menu.addItem(.separator())
            appendOmlxSection(menu, title: "oMLX (all-time)", scope: snapshot.omlxAlltime, includeRequests: true)
        }

        if snapshot.livePrefillTps != nil || snapshot.liveGenerationTps != nil {
            menu.addItem(.separator())
            addStat(menu, "Live PP", formatOptionalTps(snapshot.livePrefillTps))
            addStat(menu, "Live TG", formatOptionalTps(snapshot.liveGenerationTps))
        }
    }

    private static func appendLiveThroughput(_ menu: NSMenu, snapshot: TelemetrySnapshot) {
        let reqPerS = snapshot.liveRequestsPerS
        let genTps = snapshot.liveGenerationTps
        guard reqPerS != nil || genTps != nil else { return }
        menu.addItem(.separator())
        let header = NSMenuItem(title: "Live throughput", action: nil, keyEquivalent: "")
        header.isEnabled = false
        menu.addItem(header)
        if let reqPerS {
            addStat(menu, "req/s", String(format: "%.2f", reqPerS))
        } else {
            addStat(menu, "req/s", "—")
        }
        addStat(menu, "gen tok/s", formatOptionalTps(genTps))
    }

    private static func appendCounterSections(
        _ menu: NSMenu,
        snapshot: TelemetrySnapshot,
        status: ServingStatsStatusContext
    ) {
        let capacity = status.capacityRejections > 0
            ? status.capacityRejections
            : snapshot.capacityRejections
        let shardless = status.shardlessFallbacks > 0
            ? status.shardlessFallbacks
            : snapshot.shardlessFallbacks
        let sources = status.sourceRequests
        let scenarios = status.scenarioRequests

        guard capacity > 0 || shardless > 0 || !sources.isEmpty || !scenarios.isEmpty else { return }

        menu.addItem(.separator())
        if capacity > 0 {
            addStat(menu, "Capacity rejections", CompactCountFormatter.format(capacity), raw: capacity)
        }
        if shardless > 0 {
            addStat(menu, "Shardless fallbacks", CompactCountFormatter.format(shardless), raw: shardless)
        }
        if !sources.isEmpty {
            let header = NSMenuItem(title: "Requests by source", action: nil, keyEquivalent: "")
            header.isEnabled = false
            menu.addItem(header)
            for (key, count) in sources.sorted(by: { $0.value > $1.value }).prefix(8) {
                addStat(menu, key, CompactCountFormatter.format(count), raw: count)
            }
        }
        if !scenarios.isEmpty {
            let header = NSMenuItem(title: "Requests by scenario", action: nil, keyEquivalent: "")
            header.isEnabled = false
            menu.addItem(header)
            for (key, count) in scenarios.sorted(by: { $0.value > $1.value }).prefix(8) {
                addStat(menu, key, CompactCountFormatter.format(count), raw: count)
            }
        }
    }

    private static func appendRouterSection(
        _ menu: NSMenu,
        title: String,
        scope: [String: Any],
        includeRequests: Bool = false
    ) {
        let header = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        header.isEnabled = false
        menu.addItem(header)

        if includeRequests {
            let requests = int(scope["requests"])
            addStat(menu, "Requests", CompactCountFormatter.format(requests), raw: requests)
        }

        let prompt = int(scope["prompt_tokens"])
        let completion = int(scope["completion_tokens"])
        let totalTokens = int(scope["total_tokens"]).nonZero ?? (prompt + completion)
        addStat(menu, "Prompt tokens", CompactCountFormatter.format(prompt), raw: prompt)
        addStat(menu, "Completion tokens", CompactCountFormatter.format(completion), raw: completion)
        addStat(menu, "Total tokens", CompactCountFormatter.format(totalTokens), raw: totalTokens)
        addStat(menu, "Avg prefill", formatOptionalTps(optionalDouble(scope["avg_prefill_tps"])))
        addStat(menu, "Avg generation", formatOptionalTps(optionalDouble(scope["avg_generation_tps"])))
    }

    private static func appendOmlxSection(
        _ menu: NSMenu,
        title: String,
        scope: [String: Any],
        includeRequests: Bool = false
    ) {
        let header = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        header.isEnabled = false
        menu.addItem(header)

        let prompt = int(scope["total_prompt_tokens"]) + int(scope["prompt_tokens"])
        let completion = int(scope["total_completion_tokens"]) + int(scope["completion_tokens"])
        let totalTokens = int(scope["total_tokens"]).nonZero ?? (prompt + completion)
        addStat(menu, "Total tokens", CompactCountFormatter.format(totalTokens), raw: totalTokens)
        addStat(
            menu,
            "Cached tokens",
            CompactCountFormatter.format(int(scope["total_cached_tokens"])),
            raw: int(scope["total_cached_tokens"])
        )
        let cachePct = double(scope["cache_efficiency_pct"])
        addStat(menu, "Cache efficiency", String(format: "%.1f%%", cachePct))
        addStat(
            menu,
            "Avg PP speed",
            CompactCountFormatter.formatTps(double(scope["avg_prefill_tps"]))
        )
        addStat(
            menu,
            "Avg TG speed",
            CompactCountFormatter.formatTps(double(scope["avg_generation_tps"]))
        )
        if includeRequests {
            let requests = int(scope["total_requests"]).nonZero ?? int(scope["requests"])
            addStat(menu, "Total requests", CompactCountFormatter.format(requests), raw: requests)
        }
    }

    private static func addStat(_ menu: NSMenu, _ title: String, _ value: String, raw: Int? = nil) {
        let item = NSMenuItem(title: "\(title):  \(value)", action: nil, keyEquivalent: "")
        item.isEnabled = false
        if let raw {
            item.toolTip = CompactCountFormatter.tooltip(raw)
        }
        menu.addItem(item)
    }

    private static func int(_ value: Any?) -> Int {
        if let value = value as? Int { return value }
        if let value = value as? Double { return Int(value) }
        if let value = value as? NSNumber { return value.intValue }
        return 0
    }

    private static func double(_ value: Any?) -> Double {
        if let value = value as? Double { return value }
        if let value = value as? Int { return Double(value) }
        if let value = value as? NSNumber { return value.doubleValue }
        return 0
    }

    private static func optionalDouble(_ value: Any?) -> Double? {
        if value == nil || value is NSNull { return nil }
        if let value = value as? Double { return value }
        if let value = value as? Int { return Double(value) }
        if let value = value as? NSNumber { return value.doubleValue }
        return nil
    }

    private static func formatOptionalTps(_ value: Double?) -> String {
        guard let value else { return "—" }
        return CompactCountFormatter.formatTps(value)
    }

    private static func formatOptionalMs(_ value: Double?) -> String {
        guard let value else { return "—" }
        return String(format: "%.0f ms", value)
    }
}

private extension Int {
    var nonZero: Int? { self == 0 ? nil : self }
}
