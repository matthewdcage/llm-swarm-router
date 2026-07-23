import Foundation

enum CompactCountFormatter {
    static func format(_ value: Int) -> String {
        let n = Int64(value)
        let abs = Swift.abs(n)
        if abs >= 1_000_000_000_000 {
            return String(format: "%.1fT", Double(n) / 1_000_000_000_000.0)
        }
        if abs >= 1_000_000_000 {
            return String(format: "%.1fB", Double(n) / 1_000_000_000.0)
        }
        if abs >= 1_000_000 {
            return String(format: "%.1fM", Double(n) / 1_000_000.0)
        }
        if abs >= 1_000 {
            return String(format: "%.1fK", Double(n) / 1_000.0)
        }
        return String(n)
    }

    static func tooltip(_ value: Int) -> String {
        let formatter = NumberFormatter()
        formatter.numberStyle = .decimal
        formatter.groupingSeparator = ""
        return formatter.string(from: NSNumber(value: value)) ?? String(value)
    }

    static func formatTps(_ value: Double) -> String {
        String(format: "%.1f tok/s", value)
    }

    static func formatPercent(_ value: Double) -> String {
        String(format: "%.0f%%", value)
    }
}
