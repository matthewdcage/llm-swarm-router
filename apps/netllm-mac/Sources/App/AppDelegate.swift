import AppKit
import SwiftUI

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, AppControlHandling {
    private var server: ServerProcess?
    private var controlServer: AppControlServer?
    private var welcomeWindow: NSWindow?
    private var settingsWindow: NSWindow?
    private var aboutWindow: NSWindow?
    private var settingsModel: SettingsViewModel?
    private var runtime: PythonRuntime?

    func applicationDidFinishLaunching(_ notification: Notification) {
        let version = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "?"
        AppLogger.log("applicationDidFinishLaunching started (v\(version))")
        NSApp.setActivationPolicy(.accessory)
        updateApplicationIcon()
        observeInterfaceTheme()

        let runtime = PythonRuntime()
        self.runtime = runtime
        AppLogger.log("PythonRuntime ready")
        let config = AppConfig.load()
        server = ServerProcess(
            runtime: runtime,
            bindAddress: config.bindHost,
            port: config.port,
            configPath: config.configPath
        )
        AppLogger.log("ServerProcess configured host=\(config.bindHost) port=\(config.port)")
        controlServer = AppControlServer()
        controlServer?.handler = self
        let controlSock = AppConfig.appSupportURL().appendingPathComponent("control.sock")
        do {
            try controlServer?.start()
            AppLogger.log("control.sock listening at \(controlSock.path)")
        } catch {
            AppLogger.log("control.sock start failed: \(error.localizedDescription); retrying after unlink")
            try? FileManager.default.removeItem(at: controlSock)
            do {
                try controlServer?.start()
                AppLogger.log("control.sock listening at \(controlSock.path) (after retry)")
            } catch {
                AppLogger.log("control.sock retry failed: \(error.localizedDescription)")
            }
        }

        MenubarAppModel.shared.configure(
            server: server!,
            config: config,
            callbacks: MenubarCallbacks(
                openSettings: { [weak self] in self?.showSettings() },
                openAbout: { [weak self] in self?.showAbout() },
                openLogFile: { [weak self] in self?.openLogFile() },
                openLogFolder: { [weak self] in self?.openLogFolder() }
            )
        )
        MenubarController.shared.start(model: MenubarAppModel.shared)
        AppLogger.log("menubar created")
        ShellEnvWriter.ensureCLIShim(bundleCLI: runtime.bundleCLIPath)

        UpdateController.shared.configure(server: server!)
        UpdateNotifier.requestAuthorizationIfNeeded()
        Task {
            await UpdateController.shared.prepareCacheOnLaunch()
            UpdateController.shared.restartPollingIfNeeded()
        }

        closeStraySwiftUISettingsWindows()
        AppLogger.log("applicationDidFinishLaunching finished")

        Task { @MainActor in
            guard let server else { return }
            AppLogger.log("launch task: reconcileListeningPort adoptOrphan=\(config.autoStartOnLaunch)")
            await server.reconcileListeningPort(adoptOrphan: config.autoStartOnLaunch)
            AppLogger.log("launch task: after reconcile state=\(Self.describe(server.state))")
            if config.needsWelcome || !config.autoStartOnLaunch {
                AppLogger.log("launch task: showing welcome (needsWelcome=\(config.needsWelcome) autoStart=\(config.autoStartOnLaunch))")
                showWelcome()
            } else if config.autoStartOnLaunch {
                switch server.state {
                case .stopped, .failed:
                    do {
                        let result = try server.start()
                        AppLogger.log("launch task: auto_start result=\(result) state=\(Self.describe(server.state))")
                    } catch {
                        AppLogger.log("launch task: auto_start failed: \(error.localizedDescription)")
                    }
                default:
                    AppLogger.log("launch task: auto_start skipped state=\(Self.describe(server.state))")
                }
            }
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        AppLogger.log("applicationWillTerminate started")
        controlServer?.stop()
        AppLogger.log("control.sock stopped")
        guard let server else { return }
        // Do not block the main thread on DispatchGroup.wait while a MainActor Task
        // runs stop() — that deadlocks and leaves the agent orphaned on :11400.
        let done = DispatchSemaphore(value: 0)
        Task { @MainActor in
            await server.stop()
            done.signal()
        }
        let deadline = Date().addingTimeInterval(15)
        while done.wait(timeout: .now() + 0.05) == .timedOut, Date() < deadline {
            RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.05))
        }
        AppLogger.log("applicationWillTerminate finished state=\(Self.describe(server.state))")
    }

    private static func describe(_ state: ServerProcess.State) -> String {
        switch state {
        case .stopped: "stopped"
        case .starting: "starting"
        case .running(let pid): "running(pid=\(pid))"
        case .stopping: "stopping"
        case .failed(let message): "failed(\(message))"
        case .unresponsive(let pid): "unresponsive(pid=\(pid))"
        }
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

    /// SwiftUI `Settings { EmptyView() }` (removed) opened a blank titled window on launch for DMG installs.
    private func closeStraySwiftUISettingsWindows() {
        for window in NSApp.windows {
            if window === settingsWindow || window === welcomeWindow || window === aboutWindow {
                continue
            }
            if window.title == AppBranding.settingsTitle {
                let isEmpty = window.contentView?.subviews.isEmpty ?? true
                if isEmpty {
                    window.close()
                }
                continue
            }
            let frame = window.frame
            if frame.width < 4 && frame.height < 4 {
                window.close()
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
