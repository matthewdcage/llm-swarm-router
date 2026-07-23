import AppKit

enum ServingStatsMenuBuilder {
    static func apply(to menu: NSMenu, snapshot: TelemetrySnapshot) {
        menu.removeAllItems()
        appendSection(menu, title: "Session", scope: snapshot.routerSession)
        menu.addItem(.separator())
        appendSection(menu, title: "All-Time", scope: snapshot.routerAlltime, includeRequests: true)
    }

    private static func appendSection(
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
        addStat(menu, "Total Tokens Processed", CompactCountFormatter.format(totalTokens), raw: totalTokens)
        addStat(
            menu,
            "Cached Tokens",
            CompactCountFormatter.format(int(scope["total_cached_tokens"])),
            raw: int(scope["total_cached_tokens"])
        )
        let cachePct = double(scope["cache_efficiency_pct"])
        addStat(menu, "Cache Efficiency", String(format: "%.1f%%", cachePct))
        addStat(
            menu,
            "Avg PP Speed",
            CompactCountFormatter.formatTps(double(scope["avg_prefill_tps"]))
        )
        addStat(
            menu,
            "Avg TG Speed",
            CompactCountFormatter.formatTps(double(scope["avg_generation_tps"]))
        )
        if includeRequests {
            let requests = int(scope["total_requests"]).nonZero ?? int(scope["requests"])
            addStat(menu, "Total Requests", CompactCountFormatter.format(requests), raw: requests)
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
