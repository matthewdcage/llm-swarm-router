import AppKit

enum ServingStatsMenuBuilder {
    static func apply(to menu: NSMenu, snapshot: TelemetrySnapshot) {
        menu.removeAllItems()

        if let primary = snapshot.primaryModel, !primary.isEmpty {
            addStat(menu, "Active model", primary)
            menu.addItem(.separator())
        } else if !snapshot.loadedModels.isEmpty {
            addStat(menu, "Loaded models", snapshot.loadedModels.joined(separator: ", "))
            menu.addItem(.separator())
        }

        appendRouterSection(menu, title: "Router (session)", scope: snapshot.routerSession, includeRequests: true)
        menu.addItem(.separator())
        appendRouterSection(menu, title: "Router (all-time)", scope: snapshot.routerAlltime, includeRequests: true)

        // Time to first token, measured on the streaming path. `router.latency`
        // is one rolling window for the whole router, not per session/all-time,
        // so it is added once rather than inside appendRouterSection.
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

        let routed = snapshot.routedRequests
        if !routed.isEmpty {
            menu.addItem(.separator())
            let header = NSMenuItem(title: "Routed by backend", action: nil, keyEquivalent: "")
            header.isEnabled = false
            menu.addItem(header)
            for (key, count) in routed.sorted(by: { $0.value > $1.value }).prefix(8) {
                addStat(menu, key, CompactCountFormatter.format(count), raw: count)
            }
        }

        if snapshot.omlxAvailable {
            menu.addItem(.separator())
            appendOmlxSection(menu, title: "oMLX (session)", scope: snapshot.omlxSession)
            menu.addItem(.separator())
            appendOmlxSection(menu, title: "oMLX (all-time)", scope: snapshot.omlxAlltime, includeRequests: true)
        }

        // Live throughput is router-wide (`router.live`, with oMLX's own
        // reading preferred when its admin API answers), so it is no longer
        // gated on an oMLX backend being present — a mesh of Ollama or vLLM
        // backends has these figures too. nil is "never measured", which is
        // why the section appears at all rather than being hidden by a
        // `> 0` test that cannot tell idle from unmeasured.
        if snapshot.livePrefillTps != nil || snapshot.liveGenerationTps != nil {
            menu.addItem(.separator())
            addStat(menu, "Live PP", formatOptionalTps(snapshot.livePrefillTps))
            addStat(menu, "Live TG", formatOptionalTps(snapshot.liveGenerationTps))
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
        // Null-aware: these are measured only on streaming requests, so a mesh
        // that has only served non-streaming calls reports null, not zero.
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

    /// Numeric read that keeps "absent" distinct from zero.
    ///
    /// `double(_:)` folds a missing key and a JSON `null` into 0, which would
    /// print "0.00 tok/s" for a figure that was never measured. The agent now
    /// sends `null` for prefill/generation throughput when no streaming
    /// request has been observed (TTFT is unobservable on a non-streaming
    /// response), so coercing to zero would reintroduce exactly the invented
    /// number that was just removed from the payload.
    ///
    /// `NSNull` is what `JSONSerialization` yields for `null`; a plain `nil`
    /// covers the key being absent on an older agent.
    private static func optionalDouble(_ value: Any?) -> Double? {
        if value == nil || value is NSNull { return nil }
        if let value = value as? Double { return value }
        if let value = value as? Int { return Double(value) }
        if let value = value as? NSNumber { return value.doubleValue }
        return nil
    }

    /// Throughput, or an em dash when the agent has nothing to report.
    /// Takes `Double?` rather than `Any?` so the JSON sites have to go
    /// through `optionalDouble` explicitly and there is no overload for the
    /// compiler to pick between.
    private static func formatOptionalTps(_ value: Double?) -> String {
        guard let value else { return "—" }
        return CompactCountFormatter.formatTps(value)
    }

    /// Milliseconds, or an em dash when never measured.
    private static func formatOptionalMs(_ value: Double?) -> String {
        guard let value else { return "—" }
        return String(format: "%.0f ms", value)
    }
}

private extension Int {
    var nonZero: Int? { self == 0 ? nil : self }
}
