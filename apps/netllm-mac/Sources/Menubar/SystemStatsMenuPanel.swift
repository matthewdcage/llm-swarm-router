import AppKit

final class SystemStatsMenuPanel: NSView {
    private let width: CGFloat = 300
    private let height: CGFloat = 400

    private static let wiredColor = NSColor.systemBlue
    private static let activeColor = NSColor.systemRed
    private static let compressedColor = NSColor.systemPurple
    private static let freeColor = NSColor.quaternaryLabelColor

    override var intrinsicContentSize: NSSize {
        NSSize(width: width, height: height)
    }

    func refresh(sample: HostSample) {
        self.sample = sample
        needsDisplay = true
    }

    private var sample = HostSample()

    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)
        NSColor.windowBackgroundColor.setFill()
        bounds.fill()

        let pad: CGFloat = 14
        var y = bounds.height - pad

        y = drawSection(title: "CPU", y: y, pad: pad) { startY in
            var cursor = startY
            cursor = drawMetricBar(
                label: "E-cores",
                value: sample.eCorePct,
                color: .systemOrange,
                y: cursor,
                pad: pad
            )
            cursor = drawMetricBar(
                label: "P-cores",
                value: sample.pCorePct,
                color: .systemBlue,
                y: cursor,
                pad: pad
            )
            cursor = drawCaption(
                "E (amber) / P (blue) usage · \(HostSampler.sparklineWindow)s",
                y: cursor - 2,
                pad: pad
            )
            cursor = drawSparkline(
                primary: HostSampler.shared.eCoreHistory.suffix(HostSampler.sparklineWindow),
                primaryColor: .systemOrange,
                secondary: HostSampler.shared.pCoreHistory.suffix(HostSampler.sparklineWindow),
                secondaryColor: .systemBlue,
                y: cursor - 26,
                pad: pad
            )
            cursor -= 8
            cursor = drawLine("Thermal: \(sample.thermal)", y: cursor, pad: pad)
            let load = sample.loadAvg
            cursor = drawLine(
                String(format: "Load avg: %.2f · %.2f · %.2f", load.0, load.1, load.2),
                y: cursor,
                pad: pad
            )
            return drawLine("Uptime: \(formatUptime(sample.uptimeSeconds))", y: cursor, pad: pad)
        }

        y = drawSection(title: "GPU", y: y - 10, pad: pad) { startY in
            var cursor = startY
            cursor = drawMetricBar(label: "GPU", value: sample.gpuPct, color: .systemGreen, y: cursor, pad: pad)
            let gpuMemPct = gpuMemoryPercent()
            cursor = drawLine(
                String(format: "GPU memory: %.2f GB in use", sample.gpuMemoryGB),
                y: cursor,
                pad: pad
            )
            cursor = drawThinBar(value: gpuMemPct, color: .systemCyan, y: cursor - 4, pad: pad)
            cursor = drawCaption(
                "GPU (green) / GPU mem (cyan) · \(HostSampler.sparklineWindow)s",
                y: cursor - 2,
                pad: pad
            )
            return drawSparkline(
                primary: HostSampler.shared.gpuHistory.suffix(HostSampler.sparklineWindow),
                primaryColor: .systemGreen,
                secondary: HostSampler.shared.gpuMemoryHistory.suffix(HostSampler.sparklineWindow),
                secondaryColor: .systemCyan,
                y: cursor - 26,
                pad: pad,
                normalizeSecondaryIndependently: true
            )
        }

        _ = drawSection(title: "MEMORY", y: y - 10, pad: pad) { startY in
            var cursor = startY
            let pct = sample.memoryTotalGB > 0 ? sample.memoryUsedGB / sample.memoryTotalGB * 100 : 0
            cursor = drawMemoryHeader(
                used: sample.memoryUsedGB,
                total: sample.memoryTotalGB,
                percent: pct,
                y: cursor,
                pad: pad
            )
            cursor = drawMemoryStackBar(y: cursor - 6, pad: pad)
            cursor -= 8
            cursor = drawLegend(label: "Wired", valueGB: sample.wiredGB, color: Self.wiredColor, y: cursor, pad: pad)
            cursor = drawLegend(label: "Active", valueGB: sample.activeGB, color: Self.activeColor, y: cursor, pad: pad)
            cursor = drawLegend(
                label: "Compressed",
                valueGB: sample.compressedGB,
                color: Self.compressedColor,
                y: cursor,
                pad: pad
            )
            return drawLegend(label: "Free", valueGB: sample.freeGB, color: Self.freeColor, y: cursor, pad: pad)
        }
    }

    private func gpuMemoryPercent() -> Double {
        guard sample.memoryTotalGB > 0 else { return 0 }
        return min(100, sample.gpuMemoryGB / sample.memoryTotalGB * 100)
    }

    private func drawSection(title: String, y: CGFloat, pad: CGFloat, body: (CGFloat) -> CGFloat) -> CGFloat {
        var cursor = y
        let attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.boldSystemFont(ofSize: 12),
            .foregroundColor: NSColor.systemBlue,
        ]
        let size = (title as NSString).size(withAttributes: attrs)
        let x = (bounds.width - size.width) / 2
        title.draw(at: NSPoint(x: x, y: cursor - size.height), withAttributes: attrs)
        cursor -= size.height + 8
        return body(cursor)
    }

    private func drawLine(_ text: String, y: CGFloat, pad: CGFloat) -> CGFloat {
        let attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.monospacedSystemFont(ofSize: 11, weight: .regular),
            .foregroundColor: NSColor.labelColor,
        ]
        text.draw(at: NSPoint(x: pad, y: y - 12), withAttributes: attrs)
        return y - 16
    }

    private func drawCaption(_ text: String, y: CGFloat, pad: CGFloat) -> CGFloat {
        let attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 10),
            .foregroundColor: NSColor.secondaryLabelColor,
        ]
        text.draw(at: NSPoint(x: pad, y: y - 11), withAttributes: attrs)
        return y - 14
    }

    private func drawMetricBar(label: String, value: Double, color: NSColor, y: CGFloat, pad: CGFloat) -> CGFloat {
        let labelAttrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 11),
            .foregroundColor: NSColor.labelColor,
        ]
        let pctAttrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.monospacedDigitSystemFont(ofSize: 11, weight: .regular),
            .foregroundColor: NSColor.labelColor,
        ]
        let labelText = "\(label):"
        (labelText as NSString).draw(at: NSPoint(x: pad, y: y - 12), withAttributes: labelAttrs)
        let pctText = "\(Int(value.rounded()))%"
        let pctSize = (pctText as NSString).size(withAttributes: pctAttrs)
        pctText.draw(
            at: NSPoint(x: bounds.width - pad - pctSize.width, y: y - 12),
            withAttributes: pctAttrs
        )

        let barY = y - 24
        let barRect = NSRect(x: pad, y: barY, width: bounds.width - pad * 2, height: 8)
        NSColor.separatorColor.setFill()
        barRect.fill()
        let fillWidth = barRect.width * CGFloat(max(0, min(100, value)) / 100)
        if fillWidth > 0 {
            color.setFill()
            NSRect(x: pad, y: barY, width: fillWidth, height: 8).fill()
        }
        return barY - 8
    }

    private func drawThinBar(value: Double, color: NSColor, y: CGFloat, pad: CGFloat) -> CGFloat {
        let barY = y - 8
        let barRect = NSRect(x: pad, y: barY, width: bounds.width - pad * 2, height: 4)
        NSColor.separatorColor.setFill()
        barRect.fill()
        let fillWidth = barRect.width * CGFloat(max(0, min(100, value)) / 100)
        if fillWidth > 0 {
            color.setFill()
            NSRect(x: pad, y: barY, width: fillWidth, height: 4).fill()
        }
        return barY - 6
    }

    private func drawMemoryHeader(used: Double, total: Double, percent: Double, y: CGFloat, pad: CGFloat) -> CGFloat {
        let leftAttrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.monospacedDigitSystemFont(ofSize: 11, weight: .regular),
            .foregroundColor: NSColor.labelColor,
        ]
        let rightAttrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.monospacedDigitSystemFont(ofSize: 11, weight: .semibold),
            .foregroundColor: NSColor.labelColor,
        ]
        let left = String(format: "%.1f / %.0f GB", used, total)
        (left as NSString).draw(at: NSPoint(x: pad, y: y - 12), withAttributes: leftAttrs)
        let right = String(format: "%.0f%%", percent)
        let rightSize = (right as NSString).size(withAttributes: rightAttrs)
        right.draw(
            at: NSPoint(x: bounds.width - pad - rightSize.width, y: y - 12),
            withAttributes: rightAttrs
        )
        return y - 18
    }

    private func drawMemoryStackBar(y: CGFloat, pad: CGFloat) -> CGFloat {
        let barRect = NSRect(x: pad, y: y - 10, width: bounds.width - pad * 2, height: 10)
        NSColor.separatorColor.setFill()
        barRect.fill()
        let total = max(sample.memoryTotalGB, sample.wiredGB + sample.activeGB + sample.compressedGB + sample.freeGB)
        guard total > 0 else { return y - 14 }
        var x = barRect.minX
        let segments: [(Double, NSColor)] = [
            (sample.wiredGB, Self.wiredColor),
            (sample.activeGB, Self.activeColor),
            (sample.compressedGB, Self.compressedColor),
            (sample.freeGB, Self.freeColor),
        ]
        for (gb, color) in segments where gb > 0 {
            let width = barRect.width * CGFloat(gb / total)
            color.setFill()
            NSRect(x: x, y: barRect.minY, width: max(1, width), height: barRect.height).fill()
            x += width
        }
        return y - 16
    }

    private func drawLegend(label: String, valueGB: Double, color: NSColor, y: CGFloat, pad: CGFloat) -> CGFloat {
        let swatch = NSRect(x: pad, y: y - 10, width: 8, height: 8)
        color.setFill()
        swatch.fill()
        let text = String(format: "%@: %.2f GB", label, valueGB)
        let attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.monospacedSystemFont(ofSize: 10, weight: .regular),
            .foregroundColor: NSColor.labelColor,
        ]
        text.draw(at: NSPoint(x: pad + 14, y: y - 12), withAttributes: attrs)
        return y - 14
    }

    private func drawSparkline(
        primary: ArraySlice<Double>,
        primaryColor: NSColor,
        secondary: ArraySlice<Double>? = nil,
        secondaryColor: NSColor? = nil,
        y: CGFloat,
        pad: CGFloat,
        normalizeSecondaryIndependently: Bool = false
    ) -> CGFloat {
        let rect = NSRect(x: pad, y: y, width: bounds.width - pad * 2, height: 24)
        drawSeries(Array(primary), in: rect, color: primaryColor)
        if let secondary, let secondaryColor {
            if normalizeSecondaryIndependently {
                let maxMem = max(secondary.max() ?? 1, 0.001)
                drawSeries(Array(secondary), in: rect, color: secondaryColor, ceiling: maxMem)
            } else {
                drawSeries(Array(secondary), in: rect, color: secondaryColor)
            }
        }
        return y
    }

    private func drawSeries(_ values: [Double], in rect: NSRect, color: NSColor, ceiling: Double? = nil) {
        guard values.count > 1 else { return }
        let maxVal = max(ceiling ?? (values.max() ?? 1), 1)
        let path = NSBezierPath()
        for (idx, value) in values.enumerated() {
            let x = rect.minX + rect.width * CGFloat(idx) / CGFloat(values.count - 1)
            let y = rect.minY + rect.height * CGFloat(min(value, maxVal) / maxVal)
            if idx == 0 { path.move(to: NSPoint(x: x, y: y)) }
            else { path.line(to: NSPoint(x: x, y: y)) }
        }
        color.setStroke()
        path.lineWidth = 1.5
        path.stroke()
    }

    private func formatUptime(_ seconds: TimeInterval) -> String {
        let total = Int(seconds)
        let days = total / 86_400
        let hours = (total % 86_400) / 3_600
        if days > 0 { return "\(days)d \(hours)h" }
        let minutes = (total % 3_600) / 60
        if hours > 0 { return "\(hours)h \(minutes)m" }
        return "\(minutes)m"
    }
}

final class SystemStatsMenuItemView: NSView {
    private let panel = SystemStatsMenuPanel()

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        panel.translatesAutoresizingMaskIntoConstraints = false
        addSubview(panel)
        NSLayoutConstraint.activate([
            panel.leadingAnchor.constraint(equalTo: leadingAnchor),
            panel.trailingAnchor.constraint(equalTo: trailingAnchor),
            panel.topAnchor.constraint(equalTo: topAnchor),
            panel.bottomAnchor.constraint(equalTo: bottomAnchor),
        ])
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { nil }

    func refresh(sample: HostSample) {
        panel.refresh(sample: sample)
    }
}
