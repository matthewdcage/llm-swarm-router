import AppKit

@MainActor
final class MenubarController: NSObject, NSMenuDelegate {
    static let shared = MenubarController()

    private var statusItem: NSStatusItem?
    private var menu = NSMenu()
    private var telemetryPoller: TelemetryPoller?
    private var statsPoller: StatsPoller?
    private weak var model: MenubarAppModel?
    private let gaugeController = MenuBarGaugeController()
    private var systemStatsView: SystemStatsMenuItemView?
    private var servingStatsMenu = NSMenu()
    private var modelsMenu = NSMenu()
    private var menuOpen = false

    private override init() {
        super.init()
    }

    func start(model: MenubarAppModel) {
        self.model = model
        let host = model.connectableHost
        let port = model.agentPort
        statsPoller = StatsPoller(host: host, port: port)
        statsPoller?.onUpdate = { [weak self] in
            Task { @MainActor in self?.syncStats() }
        }
        telemetryPoller = TelemetryPoller(host: host, port: port)
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(telemetryDidUpdate),
            name: TelemetryPoller.didUpdateNotification,
            object: nil
        )
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(hostSampleDidUpdate),
            name: .hostSamplerDidUpdate,
            object: nil
        )
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(serverStateDidChange),
            name: ServerProcess.stateDidChangeNotification,
            object: nil
        )

        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        if let button = statusItem?.button {
            button.image = BrandAssets.menubarIcon(for: NSApp.effectiveAppearance)
            button.image?.isTemplate = true
        }
        menu.delegate = self
        statusItem?.menu = menu
        rebuildMenu()
        syncPollerRunning()
        gaugeController.configure(model: model, settings: model.uiSettings)
    }

    private func syncStats() {
        guard let model else { return }
        model.syncStatsFromPoller(statsPoller?.snapshot ?? StatsSnapshot())
        if menuOpen { refreshDynamicSections() }
        gaugeController.refreshTitles()
    }

    @objc private func telemetryDidUpdate() {
        guard let model, let poller = telemetryPoller else { return }
        model.updateTelemetrySnapshot(poller.snapshot)
        if menuOpen {
            ServingStatsMenuBuilder.apply(to: servingStatsMenu, snapshot: poller.snapshot)
            refreshSystemStatsPanel()
        }
        gaugeController.refreshTitles()
    }

    @objc private func hostSampleDidUpdate() {
        guard menuOpen else { return }
        refreshSystemStatsPanel()
        gaugeController.refreshTitles()
    }

    @objc private func serverStateDidChange() {
        rebuildMenu()
        syncPollerRunning()
    }

    private func syncPollerRunning() {
        guard let model else { return }
        if model.isRunning {
            statsPoller?.start()
        } else {
            statsPoller?.stop()
        }
    }

    func menuWillOpen(_ menu: NSMenu) {
        menuOpen = true
        HostSampler.shared.subscribe()
        telemetryPoller?.start()
        refreshDynamicSections()
    }

    func menuDidClose(_ menu: NSMenu) {
        menuOpen = false
        telemetryPoller?.stop()
        HostSampler.shared.unsubscribe()
    }

    private func refreshDynamicSections() {
        refreshSystemStatsPanel()
        if let snapshot = telemetryPoller?.snapshot {
            ServingStatsMenuBuilder.apply(to: servingStatsMenu, snapshot: snapshot)
        }
        rebuildModelsMenu()
    }

    private func refreshSystemStatsPanel() {
        var sample = HostSampler.shared.current
        let omlxMem = Double(model?.telemetrySnapshot.modelMemoryUsed ?? 0) / 1_073_741_824.0
        if sample.gpuMemoryGB <= 0, omlxMem > 0 {
            sample.gpuMemoryGB = omlxMem
        }
        systemStatsView?.refresh(sample: sample)
    }

    private func rebuildMenu() {
        menu.removeAllItems()
        guard let model else { return }

        let header = NSMenuItem(title: model.statusTitle, action: nil, keyEquivalent: "")
        header.isEnabled = false
        if model.isRunning {
            header.attributedTitle = NSAttributedString(
                string: model.statusTitle,
                attributes: [.foregroundColor: NSColor.systemGreen]
            )
        }
        menu.addItem(header)

        if model.isRunning {
            let stop = NSMenuItem(title: "Stop Agent", action: #selector(stopAgent), keyEquivalent: "")
            stop.image = NSImage(systemSymbolName: "stop.circle", accessibilityDescription: nil)
            menu.addItem(stop)
        } else {
            let start = NSMenuItem(title: "Start Agent", action: #selector(startAgent), keyEquivalent: "")
            start.image = NSImage(systemSymbolName: "play.circle", accessibilityDescription: nil)
            menu.addItem(start)
        }

        let modelsItem = NSMenuItem(title: "Models", action: nil, keyEquivalent: "")
        modelsItem.submenu = modelsMenu
        modelsItem.image = NSImage(systemSymbolName: "cube.box", accessibilityDescription: nil)
        menu.addItem(modelsItem)
        rebuildModelsMenu()

        let systemItem = NSMenuItem(title: "System Stats", action: nil, keyEquivalent: "")
        let systemMenu = NSMenu()
        let panelView = SystemStatsMenuItemView(frame: NSRect(x: 0, y: 0, width: 300, height: 400))
        systemStatsView = panelView
        let panelItem = NSMenuItem()
        panelItem.view = panelView
        systemMenu.addItem(panelItem)
        systemItem.submenu = systemMenu
        systemItem.image = NSImage(systemSymbolName: "cpu", accessibilityDescription: nil)
        menu.addItem(systemItem)

        let servingItem = NSMenuItem(title: "Serving Stats", action: nil, keyEquivalent: "")
        servingItem.submenu = servingStatsMenu
        servingItem.image = NSImage(systemSymbolName: "chart.bar", accessibilityDescription: nil)
        menu.addItem(servingItem)
        ServingStatsMenuBuilder.apply(to: servingStatsMenu, snapshot: model.telemetrySnapshot)

        menu.addItem(.separator())
        let dash = NSMenuItem(title: "Open Dashboard", action: #selector(openDashboard), keyEquivalent: "")
        dash.image = NSImage(systemSymbolName: "globe", accessibilityDescription: nil)
        menu.addItem(dash)
        if model.hasOmlxAdmin {
            let omlx = NSMenuItem(title: "Open oMLX Admin", action: #selector(openOmlx), keyEquivalent: "")
            omlx.image = NSImage(systemSymbolName: "cpu", accessibilityDescription: nil)
            menu.addItem(omlx)
        }
        menu.addItem(.separator())
        menu.addItem(withTitle: "Settings…", action: #selector(openSettings), keyEquivalent: ",")
        menu.addItem(withTitle: "About \(AppBranding.displayName)", action: #selector(openAbout), keyEquivalent: "")
        menu.addItem(withTitle: "Quit \(AppBranding.displayName)", action: #selector(quitApp), keyEquivalent: "q")

        for item in menu.items where item.action != nil {
            item.target = self
        }
    }

    private func rebuildModelsMenu() {
        modelsMenu.removeAllItems()
        guard let model else { return }
        let favorites = Set(model.uiSettings.model_favorites)
        let loaded = model.telemetrySnapshot.loadedModels
        let primary = model.telemetrySnapshot.primaryModel

        if !loaded.isEmpty || primary != nil {
            let loadedHeader = NSMenuItem(title: "Loaded", action: nil, keyEquivalent: "")
            loadedHeader.isEnabled = false
            modelsMenu.addItem(loadedHeader)
            if let primary, !primary.isEmpty {
                modelsMenu.addItem(disabledRow("• \(primary)"))
            }
            for name in loaded where name != primary {
                modelsMenu.addItem(disabledRow(name))
            }
            modelsMenu.addItem(.separator())
        }

        let favHeader = NSMenuItem(title: "Favorites", action: nil, keyEquivalent: "")
        favHeader.isEnabled = false
        modelsMenu.addItem(favHeader)
        let favModels = favorites.isEmpty
            ? model.stats.modelsPreview.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }
            : Array(favorites)
        if favModels.isEmpty {
            modelsMenu.addItem(disabledRow("None — star models in Settings"))
        } else {
            for name in favModels.prefix(8) {
                modelsMenu.addItem(disabledRow(String(name)))
            }
        }

        modelsMenu.addItem(.separator())
        let openDash = NSMenuItem(title: "Open Dashboard (full catalog)", action: #selector(openDashboard), keyEquivalent: "")
        openDash.target = self
        modelsMenu.addItem(openDash)
    }

    private func disabledRow(_ title: String) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        item.isEnabled = false
        return item
    }

    @objc private func startAgent() { model?.startAgent() }
    @objc private func stopAgent() { model?.stopAgent() }
    @objc private func openDashboard() { model?.openDashboard() }
    @objc private func openOmlx() { model?.openOmlx() }
    @objc private func openSettings() { model?.openSettings() }
    @objc private func openAbout() { model?.openAbout() }
    @objc private func quitApp() { model?.quitApp() }

    func refreshAppearance(settings: NetllmConfigDocument.UiSection) {
        guard let model else { return }
        gaugeController.configure(model: model, settings: settings)
    }
}
