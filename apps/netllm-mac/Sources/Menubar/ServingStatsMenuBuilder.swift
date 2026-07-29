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
            if snapshot.livePP > 0 || snapshot.liveTG > 0 {
                menu.addItem(.separator())
                addStat(menu, "Live PP", CompactCountFormatter.formatTps(snapshot.livePP))
                addStat(menu, "Live TG", CompactCountFormatter.formatTps(snapshot.liveTG))
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
        addStat(
            menu,
            "Avg prefill",
            CompactCountFormatter.formatTps(double(scope["avg_prefill_tps"]))
        )
        addStat(
            menu,
            "Avg generation",
            CompactCountFormatter.formatTps(double(scope["avg_generation_tps"]))
        )
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
}

private extension Int {
    var nonZero: Int? { self == 0 ? nil : self }
}
