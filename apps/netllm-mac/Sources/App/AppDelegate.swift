import AppKit
import SwiftUI

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, AppControlHandling {
    private var menubar: MenubarController?
    private var server: ServerProcess?
    private var controlServer: AppControlServer?
    private var welcomeWindow: NSWindow?
    private var settingsWindow: NSWindow?
    private var aboutWindow: NSWindow?
    private var settingsModel: SettingsViewModel?
    private var runtime: PythonRuntime?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        updateApplicationIcon()
        observeInterfaceTheme()

        let runtime = PythonRuntime()
        self.runtime = runtime
        let config = AppConfig.load()
        server = ServerProcess(
            runtime: runtime,
            bindAddress: config.bindHost,
            port: config.port,
            configPath: config.configPath
        )
        controlServer = AppControlServer()
        controlServer?.handler = self
        try? controlServer?.start()

        menubar = MenubarController(
            server: server!,
            config: config,
            updateController: UpdateController.shared,
            onOpenSettings: { [weak self] in
                self?.showSettings()
            },
            onOpenAbout: { [weak self] in
                self?.showAbout()
            },
            onOpenLogFile: { [weak self] in
                self?.openLogFile()
            },
            onOpenLogFolder: { [weak self] in
                self?.openLogFolder()
            }
        )
        ShellEnvWriter.ensureCLIShim(bundleCLI: runtime.bundleCLIPath)

        UpdateController.shared.configure(server: server!)
        UpdateController.shared.pruneCacheOnLaunch()
        UpdateController.shared.restartPollingIfNeeded()

        if config.needsWelcome || !config.autoStartOnLaunch {
            showWelcome()
        } else if config.autoStartOnLaunch {
            Task { try? server?.start() }
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        controlServer?.stop()
        let group = DispatchGroup()
        group.enter()
        Task {
            await server?.stop()
            group.leave()
        }
        _ = group.wait(timeout: .now() + 15)
    }

    func handleAppControl(_ command: AppControlServer.Command) async -> AppControlServer.Response {
        guard let server else {
            return .failure(status: "error", state: .stopped, server: nil, message: "Server unavailable")
        }
        switch command {
        case .start:
            do {
                let result = try server.start()
                switch result {
                case .started:
                    return .success(status: "started", state: server.state, server: server)
                case .alreadyRunning:
                    return .success(status: "already_running", state: server.state, server: server)
                case .portConflict:
                    return .failure(status: "port_conflict", state: server.state, server: server, message: "Port in use")
                }
            } catch {
                return .failure(status: "error", state: server.state, server: server, message: error.localizedDescription)
            }
        case .stop:
            await server.stop()
            return .success(status: "stopped", state: server.state, server: server)
        case .restart:
            do {
                let result = try await server.forceRestart()
                return .success(status: "restarted", state: server.state, server: server, message: "\(result)")
            } catch {
                return .failure(status: "error", state: server.state, server: server, message: error.localizedDescription)
            }
        case .status:
            return .success(status: "ok", state: server.state, server: server)
        }
    }

    func openLogFile() {
        let logDir = LogPaths.logDirFromConfigFile()
        let logFile = logDir.appendingPathComponent("agent.log")
        if FileManager.default.fileExists(atPath: logFile.path) {
            NSWorkspace.shared.activateFileViewerSelecting([logFile])
        } else {
            try? FileManager.default.createDirectory(at: logDir, withIntermediateDirectories: true)
            NSWorkspace.shared.open(logDir)
        }
    }

    func openLogFolder() {
        let logDir = LogPaths.logDirFromConfigFile()
        try? FileManager.default.createDirectory(at: logDir, withIntermediateDirectories: true)
        NSWorkspace.shared.open(logDir)
    }

    func showAbout() {
        NSApp.activate(ignoringOtherApps: true)
        if let aboutWindow {
            aboutWindow.makeKeyAndOrderFront(nil)
            return
        }
        let view = AboutView { [weak self] in
            self?.aboutWindow?.close()
            self?.aboutWindow = nil
        }
        let hosting = NSHostingController(rootView: view)
        let window = NSWindow(contentViewController: hosting)
        window.title = AppBranding.aboutTitle
        window.styleMask = [.titled, .closable]
        window.isReleasedWhenClosed = false
        window.center()
        window.makeKeyAndOrderFront(nil)
        aboutWindow = window
    }

    private func showSettings() {
        NSApp.activate(ignoringOtherApps: true)
        if let settingsWindow {
            settingsWindow.makeKeyAndOrderFront(nil)
            Task { await settingsModel?.refreshLiveData() }
            return
        }
        guard let runtime else { return }
        let model = SettingsViewModel(runtime: runtime)
        settingsModel = model
        let supervisor = AgentSupervisor(server: server!)
        let view = SettingsWindowView(
            model: model,
            supervisor: supervisor,
            updateController: UpdateController.shared
        ) { [weak self] in
            Task {
                await self?.settingsModel?.refreshLiveData()
                self?.settingsModel?.needsRestart = false
            }
        }
        let hosting = NSHostingController(rootView: view)
        let window = NSWindow(contentViewController: hosting)
        window.title = AppBranding.settingsTitle
        window.styleMask = [.titled, .closable, .resizable]
        window.setContentSize(NSSize(width: 780, height: 620))
        window.center()
        window.makeKeyAndOrderFront(nil)
        settingsWindow = window
    }

    private func updateApplicationIcon() {
        NSApp.applicationIconImage = BrandAssets.applicationIcon(for: NSApp.effectiveAppearance)
    }

    private func observeInterfaceTheme() {
        DistributedNotificationCenter.default().addObserver(
            forName: Notification.Name("AppleInterfaceThemeChangedNotification"),
            object: nil,
            queue: .main
        ) { [weak self] _ in
            guard let self else { return }
            Task { @MainActor in
                self.updateApplicationIcon()
            }
        }
    }

    private func showWelcome() {
        let view = WelcomeView(config: AppConfig.load()) { [weak self] in
            self?.welcomeWindow?.close()
            self?.welcomeWindow = nil
            if AppConfig.load().autoStartOnLaunch {
                Task { try? self?.server?.start() }
            }
        }
        let hosting = NSHostingController(rootView: view)
        let window = NSWindow(contentViewController: hosting)
        window.title = AppBranding.welcomeTitle
        window.setContentSize(NSSize(width: 520, height: 400))
        window.center()
        window.makeKeyAndOrderFront(nil)
        welcomeWindow = window
    }
}
