import AppKit

@MainActor
final class MenubarController {
    private let statusItem: NSStatusItem
    private let server: ServerProcess
    private let config: AppConfig
    private let updateController: UpdateController
    private let statsPoller: StatsPoller
    private var statsMenu: NSMenuItem?
    private var updateMenu: NSMenuItem?
    private let onOpenSettings: () -> Void
    private let onOpenAbout: () -> Void
    private let onOpenLogFile: () -> Void
    private let onOpenLogFolder: () -> Void

    init(
        server: ServerProcess,
        config: AppConfig,
        updateController: UpdateController,
        onOpenSettings: @escaping () -> Void,
        onOpenAbout: @escaping () -> Void,
        onOpenLogFile: @escaping () -> Void,
        onOpenLogFolder: @escaping () -> Void
    ) {
        self.server = server
        self.config = config
        self.updateController = updateController
        self.onOpenSettings = onOpenSettings
        self.onOpenAbout = onOpenAbout
        self.onOpenLogFile = onOpenLogFile
        self.onOpenLogFolder = onOpenLogFolder
        self.statsPoller = StatsPoller(host: config.bindHost == "0.0.0.0" ? "127.0.0.1" : config.bindHost, port: config.port)
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        updateMenubarIcon()
        observeAppearanceChanges()
        rebuildMenu()
        NotificationCenter.default.addObserver(
            forName: ServerProcess.stateDidChangeNotification,
            object: server,
            queue: .main
        ) { [weak self] _ in
            guard let self else { return }
            Task { @MainActor in
                self.rebuildMenu()
            }
        }
        statsPoller.onUpdate = { [weak self] in
            guard let self else { return }
            Task { @MainActor in
                self.refreshStatusHeader()
                self.updateStatsSubmenu()
            }
        }
        NotificationCenter.default.addObserver(
            forName: .netllmUpdateStateDidChange,
            object: updateController,
            queue: .main
        ) { [weak self] _ in
            guard let self else { return }
            Task { @MainActor in
                self.rebuildMenu()
                self.updateMenubarBadge()
            }
        }
    }

    private func rebuildMenu() {
        if case .downloading = updateController.state {
            Task { await updateController.reconcileDownloadWithDisk() }
        }
        let menu = NSMenu()
        let statusLine = statusTitle()
        let header = NSMenuItem(title: statusLine, action: nil, keyEquivalent: "")
        header.isEnabled = false
        if server.isRunning {
            header.attributedTitle = NSAttributedString(
                string: statusLine,
                attributes: [.foregroundColor: NSColor.systemGreen]
            )
        }
        menu.addItem(header)
        menu.addItem(.separator())

        if let updateLine = updateController.statusLabel {
            let updateHeader = NSMenuItem(title: updateLine, action: nil, keyEquivalent: "")
            updateHeader.isEnabled = false
            updateHeader.attributedTitle = NSAttributedString(
                string: updateLine,
                attributes: [.foregroundColor: NSColor.systemOrange]
            )
            menu.addItem(updateHeader)
        }

        if server.isRunning {
            menu.addItem(actionItem("Stop Agent", #selector(stopAgent)))
            statsMenu = NSMenuItem(title: "Routing Stats", action: nil, keyEquivalent: "")
            statsMenu?.submenu = NSMenu()
            menu.addItem(statsMenu!)
            statsPoller.start()
        } else {
            menu.addItem(actionItem("Start Agent", #selector(startAgent)))
            statsPoller.stop()
        }

        menu.addItem(.separator())
        updateMenu = NSMenuItem(title: "Updates", action: nil, keyEquivalent: "")
        updateMenu?.submenu = buildUpdateSubmenu()
        menu.addItem(updateMenu!)
        menu.addItem(.separator())
        menu.addItem(actionItem("Open Dashboard", #selector(openDashboard)))
        menu.addItem(actionItem("Open Status Page", #selector(openStatus)))
        menu.addItem(actionItem("Open oMLX Admin", #selector(openOmlx)))
        menu.addItem(actionItem("Copy Client Env", #selector(copyEnv)))
        menu.addItem(.separator())
        menu.addItem(actionItem("Open Log File", #selector(openLogFile)))
        menu.addItem(actionItem("Open Log Folder", #selector(openLogFolder)))
        menu.addItem(.separator())
        menu.addItem(actionItem("Settings…", #selector(openSettings), key: ","))
        menu.addItem(actionItem("About \(AppBranding.displayName)", #selector(showAbout)))
        menu.addItem(.separator())
        menu.addItem(actionItem("Quit \(AppBranding.displayName)", #selector(quitApp), key: "q"))

        statusItem.menu = menu
        updateStatsSubmenu()
        updateMenubarBadge()
    }

    private func buildUpdateSubmenu() -> NSMenu {
        let submenu = NSMenu()
        submenu.addItem(actionItem("Check for Updates…", #selector(checkForUpdates)))

        switch updateController.state {
        case .available(let release):
            if release.hasDMGAsset, InstallLocation.canAutoInstall() {
                submenu.addItem(actionItem("Download Update v\(release.version)…", #selector(downloadUpdate)))
            } else if release.hasDMGAsset {
                submenu.addItem(actionItem("Download v\(release.version) in Browser", #selector(openUpdateInBrowser)))
            } else {
                submenu.addItem(actionItem("Open Release v\(release.version)…", #selector(openUpdateInBrowser)))
            }
        case .readyToInstall(_, let release):
            if InstallLocation.canAutoInstall() {
                submenu.addItem(actionItem("Install Update v\(release.version)…", #selector(installUpdate)))
            } else {
                submenu.addItem(actionItem("Open Download v\(release.version)", #selector(openUpdateInBrowser)))
            }
        case .downloading(let progress):
            let label: String
            if let progress {
                label = String(format: "Downloading… %.0f%%", progress * 100)
            } else {
                label = updateController.statusLabel ?? "Downloading update…"
            }
            let item = NSMenuItem(title: label, action: nil, keyEquivalent: "")
            item.isEnabled = false
            submenu.addItem(item)
        case .installing, .checking:
            let item = NSMenuItem(title: updateController.statusLabel ?? "Working…", action: nil, keyEquivalent: "")
            item.isEnabled = false
            submenu.addItem(item)
        case .failed(let message):
            let item = NSMenuItem(title: message, action: nil, keyEquivalent: "")
            item.isEnabled = false
            submenu.addItem(item)
        case .idle:
            break
        }

        if updateController.availableRelease != nil {
            submenu.addItem(actionItem("Release Notes…", #selector(openReleaseNotes)))
        }
        return submenu
    }

    private func updateMenubarBadge() {
        guard let button = statusItem.button else { return }
        let hasUpdate: Bool
        switch updateController.state {
        case .available, .readyToInstall:
            hasUpdate = true
        default:
            hasUpdate = false
        }
        button.title = hasUpdate ? "●" : ""
        button.image?.isTemplate = true
    }

    private func statusTitle() -> String {
        switch server.state {
        case .running, .unresponsive:
            let snap = statsPoller.snapshot
            var line = "Agent: running (port \(config.port))"
            if snap.peerCount > 0 {
                line += " · \(snap.peerCount) peer\(snap.peerCount == 1 ? "" : "s")"
            }
            return line
        case .starting:
            return "Agent: starting…"
        case .stopping:
            return "Agent: stopping…"
        case .failed(let msg):
            return "Agent: failed — \(msg)"
        case .stopped:
            return "Agent: stopped"
        }
    }

    private func refreshStatusHeader() {
        guard let menu = statusItem.menu, let header = menu.items.first else { return }
        let statusLine = statusTitle()
        header.title = statusLine
        if server.isRunning {
            header.attributedTitle = NSAttributedString(
                string: statusLine,
                attributes: [.foregroundColor: NSColor.systemGreen]
            )
        } else {
            header.attributedTitle = NSAttributedString(string: statusLine)
        }
    }

    private func updateStatsSubmenu() {
        guard let submenu = statsMenu?.submenu else { return }
        submenu.removeAllItems()
        let snap = statsPoller.snapshot
        submenu.addItem(statsIndicatorItem(
            "Backends: \(snap.onlineBackendCount)/\(snap.backendCount) online",
            online: snap.onlineBackendCount > 0
        ))
        submenu.addItem(statsIndicatorItem(
            "Peers: \(snap.peerCount)",
            online: snap.peerCount > 0
        ))
        submenu.addItem(disabledItem("Role: \(snap.role)"))
        for backend in snap.backends {
            submenu.addItem(statsIndicatorItem(
                "\(backend.provider): \(backend.health) (\(backend.modelCount) models)",
                online: backend.health == "online"
            ))
        }
        if !snap.modelsPreview.isEmpty {
            submenu.addItem(disabledItem("Models: \(snap.modelsPreview)"))
        }
    }

    private func statsIndicatorItem(_ title: String, online: Bool) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        item.isEnabled = false
        let color = online ? NSColor.systemGreen : NSColor.secondaryLabelColor
        item.attributedTitle = NSAttributedString(
            string: (online ? "● " : "○ ") + title,
            attributes: [.foregroundColor: color]
        )
        return item
    }

    private func actionItem(_ title: String, _ sel: Selector, key: String = "") -> NSMenuItem {
        let item = NSMenuItem(title: title, action: sel, keyEquivalent: key)
        if key == "," {
            item.keyEquivalentModifierMask = .command
        }
        item.target = self
        return item
    }

    private func updateMenubarIcon() {
        guard let button = statusItem.button else { return }
        if let icon = BrandAssets.menubarIcon(for: NSApp.effectiveAppearance) {
            button.image = icon
        } else {
            button.image = NSImage(
                systemSymbolName: "network",
                accessibilityDescription: AppBranding.displayName
            )
        }
        button.image?.accessibilityDescription = AppBranding.displayName
    }

    private func observeAppearanceChanges() {
        let center = NotificationCenter.default
        center.addObserver(
            forName: NSApplication.didChangeScreenParametersNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            guard let self else { return }
            Task { @MainActor in
                self.updateMenubarIcon()
            }
        }
        DistributedNotificationCenter.default().addObserver(
            forName: Notification.Name("AppleInterfaceThemeChangedNotification"),
            object: nil,
            queue: .main
        ) { [weak self] _ in
            guard let self else { return }
            Task { @MainActor in
                self.updateMenubarIcon()
            }
        }
    }

    private func disabledItem(_ title: String) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        item.isEnabled = false
        return item
    }

    @objc private func startAgent() {
        Task {
            if case .failed = server.state {
                await server.reconcileListeningPort(adoptOrphan: true)
                if server.isRunning { return }
                try? await server.forceRestart()
            } else {
                try? server.start()
            }
        }
    }
    @objc private func stopAgent() { Task { await server.stop() } }
    @objc private func openDashboard() {
        let host = AppConfig.connectableHost(for: config.bindHost)
        NSWorkspace.shared.open(URL(string: "http://\(host):\(config.port)/ui/")!)
    }
    @objc private func openStatus() {
        let host = AppConfig.connectableHost(for: config.bindHost)
        NSWorkspace.shared.open(URL(string: "http://\(host):\(config.port)/")!)
    }
    @objc private func openOmlx() {
        NSWorkspace.shared.open(URL(string: "http://127.0.0.1:8080/admin")!)
    }
    @objc private func copyEnv() {
        let host = AppConfig.connectableHost(for: config.bindHost)
        let text = """
        export OPENAI_BASE_URL=http://\(host):\(config.port)/v1
        export OPENAI_API_KEY=netllm-local
        export ANTHROPIC_BASE_URL=http://\(host):\(config.port)
        export ANTHROPIC_API_KEY=netllm-local
        """
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
    }
    @objc private func checkForUpdates() {
        Task { await updateController.checkOnce(force: true) }
    }

    @objc private func downloadUpdate() {
        guard case .available(let release) = updateController.state else { return }
        updateController.downloadUpdate(release: release)
    }

    @objc private func installUpdate() {
        Task { await updateController.installFromReadyState() }
    }

    @objc private func openUpdateInBrowser() {
        guard let release = updateController.availableRelease else { return }
        updateController.openDownloadInBrowser(for: release)
    }

    @objc private func openReleaseNotes() {
        guard let release = updateController.availableRelease else { return }
        updateController.openReleaseNotes(for: release)
    }

    @objc private func openSettings() { onOpenSettings() }
    @objc private func openLogFile() { onOpenLogFile() }
    @objc private func openLogFolder() { onOpenLogFolder() }
    @objc private func showAbout() { onOpenAbout() }
    @objc private func quitApp() {
        Task { @MainActor in
            await server.stop()
            NSApp.terminate(nil)
        }
    }
}
