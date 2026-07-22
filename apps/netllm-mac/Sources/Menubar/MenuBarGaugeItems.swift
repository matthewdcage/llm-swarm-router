import AppKit

@MainActor
final class MenuBarGaugeController {
    private var items: [String: NSStatusItem] = [:]
    private weak var model: MenubarAppModel?

    func configure(model: MenubarAppModel, settings: NetllmConfigDocument.UiSection) {
        self.model = model
        rebuild(settings: settings)
    }

    func rebuild(settings: NetllmConfigDocument.UiSection) {
        clear()
        guard settings.menubar_show_cpu || settings.menubar_show_gpu ||
            settings.menubar_show_mem || settings.menubar_show_live else { return }

        HostSampler.shared.subscribe()
        if settings.menubar_show_cpu { addGauge(id: "cpu", title: "CPU") { self.cpuTitle() } }
        if settings.menubar_show_gpu { addGauge(id: "gpu", title: "GPU") { self.gpuTitle() } }
        if settings.menubar_show_mem { addGauge(id: "mem", title: "MEM") { self.memTitle() } }
        if settings.menubar_show_live { addGauge(id: "liv", title: "LIV") { self.liveTitle() } }
    }

    func refreshTitles() {
        for (id, item) in items {
            switch id {
            case "cpu": item.button?.title = cpuTitle()
            case "gpu": item.button?.title = gpuTitle()
            case "mem": item.button?.title = memTitle()
            case "liv": item.button?.title = liveTitle()
            default: break
            }
        }
    }

    func clear() {
        for item in items.values { NSStatusBar.system.removeStatusItem(item) }
        items.removeAll()
        HostSampler.shared.unsubscribe()
    }

    private func addGauge(id: String, title: String, value: @escaping () -> String) {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.font = NSFont.monospacedDigitSystemFont(ofSize: 11, weight: .regular)
        item.button?.title = value()
        items[id] = item
    }

    private func cpuTitle() -> String {
        let s = HostSampler.shared.current
        return "CPU \(Int(s.eCorePct))/\(Int(s.pCorePct))"
    }

    private func gpuTitle() -> String {
        "GPU \(Int(HostSampler.shared.current.gpuPct))%"
    }

    private func memTitle() -> String {
        let s = HostSampler.shared.current
        guard s.memoryTotalGB > 0 else { return "MEM —" }
        let pct = Int(s.memoryUsedGB / s.memoryTotalGB * 100)
        return "MEM \(pct)%"
    }

    private func liveTitle() -> String {
        guard let model else { return "LIV —" }
        let snap = model.telemetrySnapshot
        return String(format: "P:%.0f T:%.0f", snap.livePP, snap.liveTG)
    }
}
