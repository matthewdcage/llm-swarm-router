import AppKit
import SwiftUI

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
    private var headerPrimaryItem: NSMenuItem?
    private var headerSecondaryItem: NSMenuItem?
    private var drainMenuItem: NSMenuItem?
    private var usePopover = false
    private var popover: NSPopover?
    private var popoverHostingView: NSView?

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
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(updateStateDidChange),
            name: .netllmUpdateStateDidChange,
            object: model.updateController
        )

        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        if let button = statusItem?.button {
            button.image = BrandAssets.menubarIcon(for: NSApp.effectiveAppearance)
            button.image?.isTemplate = true
            configureStatusButton(button)
        }
        menu.delegate = self
        statusItem?.menu = menu
        rebuildMenu()
        syncPollerRunning()
        gaugeController.configure(model: model, settings: model.uiSettings)
    }

    /// Phase 2: enable NSPopover shell (design 1f). When enabled, left-click
    /// opens the popover; right-click still opens the AppKit menu fallback.
    func setPopoverEnabled(_ enabled: Bool) {
        usePopover = enabled
        if let button = statusItem?.button {
            configureStatusButton(button)
        }
        if enabled {
            popover?.performClose(nil)
        }
    }

    private func configureStatusButton(_ button: NSStatusBarButton) {
        if usePopover {
            button.action = #selector(statusItemClicked(_:))
            button.target = self
            button.sendAction(on: [.leftMouseUp, .rightMouseUp])
        } else {
            button.action = nil
            button.target = nil
        }
    }

    @objc private func statusItemClicked(_ sender: NSStatusBarButton) {
        guard usePopover, let model else { return }
        let event = NSApp.currentEvent
        if event?.type == .rightMouseUp {
            menu.popUp(
                positioning: nil,
                at: NSPoint(x: 0, y: sender.bounds.height),
                in: sender
            )
            return
        }
        togglePopover(relativeTo: sender.bounds, of: sender, model: model)
    }

    private func togglePopover(relativeTo rect: NSRect, of view: NSView, model: MenubarAppModel) {
        if popover == nil {
            let pop = NSPopover()
            pop.behavior = .transient
            pop.contentSize = NSSize(width: DesignTokens.popoverWidth, height: 520)
            pop.contentViewController = NSHostingController(
                rootView: MenubarPopoverView(model: model)
            )
            pop.delegate = self
            popover = pop
        }
        guard let popover else { return }
        if popover.isShown {
            popover.performClose(nil)
        } else {
            HostSampler.shared.subscribe()
            telemetryPoller?.start()
            popover.show(relativeTo: rect, of: view, preferredEdge: .minY)
        }
    }

    private func syncStats() {
        guard let model else { return }
        model.syncStatsFromPoller(statsPoller?.snapshot ?? StatsSnapshot())
        refreshHeaderItems()
        updateDrainMenuTitle()
        if menuOpen { refreshDynamicSections() }
        gaugeController.refreshTitles()
    }

    @objc private func telemetryDidUpdate() {
        guard let model, let poller = telemetryPoller else { return }
        model.updateTelemetrySnapshot(poller.snapshot)
        refreshHeaderItems()
        if menuOpen {
            refreshServingStatsMenu()
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

    @objc private func updateStateDidChange() {
        rebuildMenu()
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
        refreshHeaderItems()
        refreshSystemStatsPanel()
        refreshServingStatsMenu()
        rebuildModelsMenu()
    }

    private func refreshServingStatsMenu() {
        guard let model else { return }
        let snapshot = telemetryPoller?.snapshot ?? model.telemetrySnapshot
        ServingStatsMenuBuilder.apply(
            to: servingStatsMenu,
            snapshot: snapshot,
            status: model.servingStatsStatusContext
        )
    }

    private func refreshSystemStatsPanel() {
        var sample = HostSampler.shared.current
        let omlxMem = Double(model?.telemetrySnapshot.modelMemoryUsed ?? 0) / 1_073_741_824.0
        if sample.gpuMemoryGB <= 0, omlxMem > 0 {
            sample.gpuMemoryGB = omlxMem
        }
        systemStatsView?.refresh(sample: sample)
    }

    private func refreshHeaderItems() {
        guard let model else { return }
        let color = MenubarStatusFormatter.headerColor(
            for: MenubarStatusFormatter.Context(
                state: model.serverProcess?.state ?? .stopped,
                port: model.agentPort,
                stats: model.stats,
                primaryModel: model.telemetrySnapshot.primaryModel
            )
        )
        headerPrimaryItem?.attributedTitle = NSAttributedString(
            string: model.statusTitle,
            attributes: [.foregroundColor: color]
        )
        if let subtitle = model.statusSubtitle, let headerSecondaryItem {
            headerSecondaryItem.isHidden = false
            headerSecondaryItem.title = subtitle
        } else {
            headerSecondaryItem?.isHidden = true
        }
    }

    private func updateDrainMenuTitle() {
        guard let model, let drainMenuItem else { return }
        drainMenuItem.title = model.stats.draining ? "Resume Agent" : "Drain Agent"
    }

    private func rebuildMenu() {
        menu.removeAllItems()
        guard let model else { return }

        let header = NSMenuItem(title: model.statusTitle, action: nil, keyEquivalent: "")
        header.isEnabled = false
        headerPrimaryItem = header
        applyHeaderStyle(to: header, model: model)
        menu.addItem(header)

        if let subtitle = model.statusSubtitle {
            let sub = NSMenuItem(title: subtitle, action: nil, keyEquivalent: "")
            sub.isEnabled = false
            headerSecondaryItem = sub
            menu.addItem(sub)
        } else {
            headerSecondaryItem = nil
        }

        if model.isRunning {
            let drain = NSMenuItem(
                title: model.stats.draining ? "Resume Agent" : "Drain Agent",
                action: #selector(toggleDrain),
                keyEquivalent: ""
            )
            drain.image = NSImage(
                systemSymbolName: model.stats.draining ? "play.circle" : "pause.circle",
                accessibilityDescription: nil
            )
            drainMenuItem = drain
            menu.addItem(drain)

            let restart = NSMenuItem(title: "Restart Agent", action: #selector(restartAgent), keyEquivalent: "")
            restart.image = NSImage(systemSymbolName: "arrow.clockwise.circle", accessibilityDescription: nil)
            menu.addItem(restart)

            let stop = NSMenuItem(title: "Stop Agent", action: #selector(stopAgent), keyEquivalent: "")
            stop.image = NSImage(systemSymbolName: "stop.circle", accessibilityDescription: nil)
            menu.addItem(stop)
        } else {
            drainMenuItem = nil
            let start = NSMenuItem(title: "Start Agent", action: #selector(startAgent), keyEquivalent: "")
            start.image = NSImage(systemSymbolName: "play.circle", accessibilityDescription: nil)
            menu.addItem(start)
        }

        let copyEnv = NSMenuItem(title: "Copy client env", action: #selector(copyEnv), keyEquivalent: "")
        copyEnv.image = NSImage(systemSymbolName: "doc.on.doc", accessibilityDescription: nil)
        menu.addItem(copyEnv)

        menu.addItem(.separator())

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
        refreshServingStatsMenu()

        menu.addItem(.separator())
        let dash = NSMenuItem(title: "Open Dashboard", action: #selector(openDashboard), keyEquivalent: "")
        dash.image = NSImage(systemSymbolName: "globe", accessibilityDescription: nil)
        menu.addItem(dash)
        if model.hasOmlxAdmin {
            let omlx = NSMenuItem(title: "Open oMLX Admin", action: #selector(openOmlx), keyEquivalent: "")
            omlx.image = NSImage(systemSymbolName: "cpu", accessibilityDescription: nil)
            menu.addItem(omlx)
        }

        if !model.hasUpdateBadge {
            let updates = NSMenuItem(
                title: "Check for Updates…",
                action: #selector(checkForUpdates),
                keyEquivalent: ""
            )
            updates.image = NSImage(systemSymbolName: "arrow.down.circle", accessibilityDescription: nil)
            menu.addItem(updates)
        }

        menu.addItem(.separator())
        menu.addItem(withTitle: "Settings…", action: #selector(openSettings), keyEquivalent: ",")
        menu.addItem(withTitle: "About \(AppBranding.displayName)", action: #selector(openAbout), keyEquivalent: "")
        menu.addItem(withTitle: "Quit \(AppBranding.displayName)", action: #selector(quitApp), keyEquivalent: "q")

        for item in menu.items where item.action != nil {
            item.target = self
        }
    }

    private func applyHeaderStyle(to header: NSMenuItem, model: MenubarAppModel) {
        let color = MenubarStatusFormatter.headerColor(
            for: MenubarStatusFormatter.Context(
                state: model.serverProcess?.state ?? .stopped,
                port: model.agentPort,
                stats: model.stats,
                primaryModel: model.telemetrySnapshot.primaryModel
            )
        )
        header.attributedTitle = NSAttributedString(
            string: model.statusTitle,
            attributes: [.foregroundColor: color]
        )
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
        let openDash = NSMenuItem(
            title: "Open Dashboard (full catalog)",
            action: #selector(openDashboard),
            keyEquivalent: ""
        )
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
    @objc private func restartAgent() { model?.restartAgent() }
    @objc private func toggleDrain() { model?.toggleDrain() }
    @objc private func copyEnv() { model?.copyEnv() }
    @objc private func checkForUpdates() { model?.checkForUpdates() }
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

extension MenubarController: NSPopoverDelegate {
    func popoverDidClose(_ notification: Notification) {
        telemetryPoller?.stop()
        HostSampler.shared.unsubscribe()
    }
}
