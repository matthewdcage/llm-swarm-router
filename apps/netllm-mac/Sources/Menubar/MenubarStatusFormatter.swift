import AppKit

/// Compact masthead lines for the menubar header — mirrors web Home masthead
/// without duplicating the full Settings Home layout.
enum MenubarStatusFormatter {
    struct Context: Sendable {
        var state: ServerProcess.State
        var port: Int
        var stats: StatsSnapshot
        var primaryModel: String?
    }

    static func headerLines(for ctx: Context) -> (primary: String, secondary: String?) {
        switch ctx.state {
        case .running, .unresponsive:
            break
        case .starting:
            return ("Agent starting…", nil)
        case .stopping:
            return ("Agent stopping…", nil)
        case .failed(let msg):
            return ("Agent failed — \(msg)", nil)
        case .stopped:
            return ("Agent stopped", nil)
        }

        let stats = ctx.stats
        var primaryParts: [String] = []
        if stats.draining {
            primaryParts.append("Draining")
        } else if stats.reachable == false {
            primaryParts.append("Unreachable")
        } else {
            primaryParts.append("Serving")
        }
        primaryParts.append("· :\(ctx.port)")
        primaryParts.append("· \(stats.role)")
        primaryParts.append("· \(stats.routingStrategy.isEmpty ? "routing" : stats.routingStrategy)")

        var secondaryParts: [String] = []
        if stats.onlineBackendCount > 0 || stats.backendCount > 0 {
            secondaryParts.append(
                "\(stats.onlineBackendCount)/\(stats.backendCount) backend\(stats.backendCount == 1 ? "" : "s")"
            )
        }
        if stats.peerCount > 0 {
            secondaryParts.append("\(stats.peerCount) peer\(stats.peerCount == 1 ? "" : "s")")
        }
        if let model = ctx.primaryModel, !model.isEmpty {
            secondaryParts.append(model)
        } else if let loaded = stats.omlxLoadedModel, !loaded.isEmpty {
            secondaryParts.append(loaded)
        }

        let secondary = secondaryParts.isEmpty ? nil : secondaryParts.joined(separator: " · ")
        return (primaryParts.joined(separator: " "), secondary)
    }

    static func headerColor(for ctx: Context) -> NSColor {
        switch ctx.state {
        case .running, .unresponsive:
            if ctx.stats.draining { return .systemOrange }
            if ctx.stats.reachable == false { return .systemRed }
            return .systemGreen
        case .starting, .stopping:
            return .secondaryLabelColor
        case .failed:
            return .systemRed
        case .stopped:
            return .secondaryLabelColor
        }
    }
}
