import AppKit
import SwiftUI

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, AppControlHandling {
    private var menubar: MenubarController?
    private var server: ServerProcess?
    private var controlServer: AppControlServer?
    private var welcomeWindow: NSWindow?
    private var settingsWindow: NSWindow?
    private var settingsModel: SettingsViewModel?
    private var runtime: PythonRuntime?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        if let icon = BrandAssets.applicationIcon() {
            NSApp.applicationIconImage = icon
        }

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

        menubar = MenubarController(server: server!, config: config) { [weak self] in
            self?.showSettings()
        }
        ShellEnvWriter.ensureCLIShim(bundleCLI: runtime.bundleCLIPath)

        if config.needsWelcome || !config.autoStartOnLaunch {
            showWelcome()
        } else if config.autoStartOnLaunch {
            Task { try? server?.start() }
        }

        UpdateController.shared.startPolling()
    }

    func applicationWillTerminate(_ notification: Notification) {
        controlServer?.stop()
        Task {
            await server?.stop()
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
        let view = SettingsWindowView(model: model) { [weak self] in
            Task {
                _ = try? await self?.server?.forceRestart()
                await self?.settingsModel?.refreshLiveData()
                self?.settingsModel?.needsRestart = false
            }
        }
        let hosting = NSHostingController(rootView: view)
        let window = NSWindow(contentViewController: hosting)
        window.title = "netllm Settings"
        window.styleMask = [.titled, .closable, .resizable]
        window.setContentSize(NSSize(width: 720, height: 560))
        window.center()
        window.makeKeyAndOrderFront(nil)
        settingsWindow = window
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
        window.title = "Welcome to netllm"
        window.setContentSize(NSSize(width: 520, height: 400))
        window.center()
        window.makeKeyAndOrderFront(nil)
        welcomeWindow = window
    }
}
