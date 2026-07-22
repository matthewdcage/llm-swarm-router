import AppKit

final class SystemStatsMenuPanel: NSView {
    private let width: CGFloat = 280
    private let height: CGFloat = 320

    override var intrinsicContentSize: NSSize {
        NSSize(width: width, height: height)
    }

    func refresh(sample: HostSample, gpuMemoryGB: Double) {
        self.sample = sample
        self.gpuMemoryGB = gpuMemoryGB
        needsDisplay = true
    }

    private var sample = HostSample()
    private var gpuMemoryGB: Double = 0

    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)
        NSColor.windowBackgroundColor.setFill()
        bounds.fill()

        let pad: CGFloat = 12
        var y = bounds.height - pad
        y = drawSection(title: "CPU", y: y, pad: pad) { startY in
            var cursor = startY
            cursor = drawBar(label: "E-cores", value: sample.eCorePct, color: .systemOrange, y: cursor, pad: pad)
            cursor = drawBar(label: "P-cores", value: sample.pCorePct, color: .systemBlue, y: cursor, pad: pad)
            cursor = drawSparkline(
                values: HostSampler.shared.eCoreHistory,
                color: .systemOrange,
                y: cursor - 28,
                pad: pad,
                secondary: HostSampler.shared.pCoreHistory,
                secondaryColor: .systemBlue
            )
            cursor -= 36
            cursor = drawLine("Thermal: \(sample.thermal)", y: cursor, pad: pad)
            let load = sample.loadAvg
            cursor = drawLine(
                String(format: "Load avg: %.2f · %.2f · %.2f", load.0, load.1, load.2),
                y: cursor,
                pad: pad
            )
            let uptime = formatUptime(sample.uptimeSeconds)
            return drawLine("Uptime: \(uptime)", y: cursor, pad: pad)
        }

        y = drawSection(title: "GPU", y: y - 8, pad: pad) { startY in
            var cursor = startY
            cursor = drawBar(label: "GPU", value: sample.gpuPct, color: .systemGreen, y: cursor, pad: pad)
            cursor = drawLine(String(format: "GPU memory: %.2f GB", gpuMemoryGB), y: cursor, pad: pad)
            return drawSparkline(values: HostSampler.shared.gpuHistory, color: .systemGreen, y: cursor - 24, pad: pad)
        }

        _ = drawSection(title: "MEMORY", y: y - 8, pad: pad) { startY in
            var cursor = startY
            let pct = sample.memoryTotalGB > 0 ? sample.memoryUsedGB / sample.memoryTotalGB * 100 : 0
            cursor = drawLine(
                String(
                    format: "%.1f / %.0f GB (%.0f%%)",
                    sample.memoryUsedGB,
                    sample.memoryTotalGB,
                    pct
                ),
                y: cursor,
                pad: pad
            )
            cursor = drawLine(String(format: "Wired: %.2f GB", sample.wiredGB), y: cursor, pad: pad)
            cursor = drawLine(String(format: "Active: %.2f GB", sample.activeGB), y: cursor, pad: pad)
            cursor = drawLine(String(format: "Compressed: %.2f GB", sample.compressedGB), y: cursor, pad: pad)
            return drawLine(String(format: "Free: %.2f GB", sample.freeGB), y: cursor, pad: pad)
        }
    }

    private func drawSection(title: String, y: CGFloat, pad: CGFloat, body: (CGFloat) -> CGFloat) -> CGFloat {
        var cursor = y
        let attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.boldSystemFont(ofSize: 12),
            .foregroundColor: NSColor.systemBlue,
        ]
        title.draw(at: NSPoint(x: pad, y: cursor - 14), withAttributes: attrs)
        cursor -= 22
        return body(cursor)
    }

    private func drawLine(_ text: String, y: CGFloat, pad: CGFloat) -> CGFloat {
        let attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 11),
            .foregroundColor: NSColor.labelColor,
        ]
        text.draw(at: NSPoint(x: pad, y: y - 12), withAttributes: attrs)
        return y - 16
    }

    private func drawBar(label: String, value: Double, color: NSColor, y: CGFloat, pad: CGFloat) -> CGFloat {
        _ = drawLine("\(label): \(Int(value))%", y: y, pad: pad)
        let barY = y - 28
        let barRect = NSRect(x: pad, y: barY, width: bounds.width - pad * 2, height: 8)
        NSColor.separatorColor.setFill()
        barRect.fill()
        let fill = NSRect(x: pad, y: barY, width: (barRect.width * value / 100), height: 8)
        color.setFill()
        fill.fill()
        return barY - 6
    }

    private func drawSparkline(
        values: [Double],
        color: NSColor,
        y: CGFloat,
        pad: CGFloat,
        secondary: [Double]? = nil,
        secondaryColor: NSColor? = nil
    ) -> CGFloat {
        let rect = NSRect(x: pad, y: y, width: bounds.width - pad * 2, height: 24)
        drawSeries(values, in: rect, color: color)
        if let secondary, let secondaryColor {
            drawSeries(secondary, in: rect, color: secondaryColor)
        }
        return y
    }

    private func drawSeries(_ values: [Double], in rect: NSRect, color: NSColor) {
        guard values.count > 1 else { return }
        let maxVal = max(values.max() ?? 1, 1)
        let path = NSBezierPath()
        for (idx, value) in values.enumerated() {
            let x = rect.minX + rect.width * CGFloat(idx) / CGFloat(values.count - 1)
            let y = rect.minY + rect.height * CGFloat(value / maxVal)
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

    func refresh(sample: HostSample, gpuMemoryGB: Double) {
        panel.refresh(sample: sample, gpuMemoryGB: gpuMemoryGB)
    }
}
