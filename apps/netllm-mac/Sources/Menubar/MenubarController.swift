import AppKit

@MainActor
final class MenubarController {
    private let statusItem: NSStatusItem
    private let server: ServerProcess
    private let config: AppConfig
    private let statsPoller: StatsPoller
    private var statsMenu: NSMenuItem?
    private let onOpenSettings: () -> Void

    init(server: ServerProcess, config: AppConfig, onOpenSettings: @escaping () -> Void) {
        self.server = server
        self.config = config
        self.onOpenSettings = onOpenSettings
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
            Task { @MainActor in self?.rebuildMenu() }
        }
        statsPoller.onUpdate = { [weak self] in
            Task { @MainActor in
                self?.refreshStatusHeader()
                self?.updateStatsSubmenu()
            }
        }
    }

    private func rebuildMenu() {
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
        menu.addItem(actionItem("Open Status Page", #selector(openStatus)))
        menu.addItem(actionItem("Open oMLX Admin", #selector(openOmlx)))
        menu.addItem(actionItem("Copy Client Env", #selector(copyEnv)))
        menu.addItem(.separator())
        menu.addItem(actionItem("Settings…", #selector(openSettings), key: ","))
        menu.addItem(actionItem("About \(AppBranding.displayName)", #selector(showAbout)))
        menu.addItem(.separator())
        menu.addItem(actionItem("Quit \(AppBranding.displayName)", #selector(quitApp), key: "q"))

        statusItem.menu = menu
        updateStatsSubmenu()
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
        let appearance = button.effectiveAppearance
        if let icon = BrandAssets.menubarIcon(for: appearance) {
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
            Task { @MainActor in self?.updateMenubarIcon() }
        }
        DistributedNotificationCenter.default().addObserver(
            forName: Notification.Name("AppleInterfaceThemeChangedNotification"),
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in self?.updateMenubarIcon() }
        }
    }

    private func disabledItem(_ title: String) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        item.isEnabled = false
        return item
    }

    @objc private func startAgent() { Task { try? server.start() } }
    @objc private func stopAgent() { Task { await server.stop() } }
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
    @objc private func openSettings() { onOpenSettings() }
    @objc private func showAbout() {
        if let icon = BrandAssets.aboutIcon() {
            NSApp.applicationIconImage = icon
        }
        NSApp.orderFrontStandardAboutPanel(options: [
            .applicationName: AppBranding.displayName,
            .applicationVersion: Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "0.2.0",
            .credits: NSAttributedString(
                string: "\(AppBranding.tagline)\nCLI: \(AppBranding.cliCommand)",
                attributes: [.font: NSFont.systemFont(ofSize: NSFont.smallSystemFontSize)]
            ),
        ])
    }
    @objc private func quitApp() { NSApp.terminate(nil) }
}
