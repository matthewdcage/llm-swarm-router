import AppKit
import CryptoKit
import Foundation
import Observation

extension Notification.Name {
    static let netllmUpdateStateDidChange = Notification.Name("NetllmUpdateStateDidChange")
}

struct GitHubRelease: Sendable, Equatable {
    let version: String
    let downloadURL: URL
    let assetName: String
    let assetSize: Int
    let sha256URL: URL?
    let releaseNotesURL: URL
    /// False when the release exists but has no macOS DMG — downloadURL points at the release page.
    let hasDMGAsset: Bool
}

@MainActor
@Observable
final class UpdateController {
    static let shared = UpdateController()

    enum UpdateState: Equatable {
        case idle
        case checking
        case available(GitHubRelease)
        case downloading(progress: Double?)
        case readyToInstall(localDMG: URL, release: GitHubRelease)
        case installing
        case failed(String)
    }

    private(set) var state: UpdateState = .idle

    private let repo = "matthewdcage/llm-swarm-router"
    private let preferredAssetName = "llm-swarm-router.dmg"
    private var pollTask: Task<Void, Never>?
    private var server: ServerProcess?
    private let checker = ReleasesChecker()
    private var activeDownloader: UpdateDownloader?
    private var activeDownloadRelease: GitHubRelease?
    private var downloadTask: Task<Void, Never>?

    private static let downloadTimeoutSeconds: UInt64 = 900 // 15 minutes

    private var cacheDirectory: URL {
        let base = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first!
        return base.appendingPathComponent("com.netllm.mac/updates", isDirectory: true)
    }

    private var currentVersion: String {
        Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "0.0.0"
    }

    private var userAgent: String {
        "netllm/\(currentVersion)"
    }

    func configure(server: ServerProcess) {
        self.server = server
    }

    /// On launch: drop partial downloads, then attach ready-to-install if a complete DMG is cached.
    func prepareCacheOnLaunch() async {
        UpdateLogger.log("prepareCacheOnLaunch (app v\(currentVersion))")
        prunePartialDownloadsOnly()
        await reconcileDownloadWithDisk()
    }

    /// Promote a verified cached DMG to ready-to-install regardless of download UI state.
    func reconcileDownloadWithDisk() async {
        if case .readyToInstall = state { return }
        if case .installing = state { return }

        let release: GitHubRelease?
        if let activeDownloadRelease {
            release = activeDownloadRelease
        } else if case .available(let available) = state {
            release = available
        } else {
            release = await checker.latestRelease(userAgent: userAgent)
        }
        guard let release,
              release.version.compare(currentVersion, options: .numeric) == .orderedDescending else {
            return
        }
        let destination = cachedDMGURL(for: release)
        if await adoptCachedDMGIfValid(destination: destination, release: release, notify: false) {
            UpdateLogger.log("reconciled cache → readyToInstall v\(release.version)")
        }
    }

    private func prunePartialDownloadsOnly() {
        let items = (try? FileManager.default.contentsOfDirectory(
            at: cacheDirectory,
            includingPropertiesForKeys: nil
        )) ?? []
        for item in items where item.pathExtension == "download" {
            try? FileManager.default.removeItem(at: item)
        }
    }

    private func cachedDMGURL(for release: GitHubRelease) -> URL {
        cacheDirectory.appendingPathComponent(
            "\(preferredAssetName.replacingOccurrences(of: ".dmg", with: ""))-\(release.version).dmg"
        )
    }

    private func fileSize(at url: URL) -> Int? {
        guard FileManager.default.fileExists(atPath: url.path),
              let attrs = try? FileManager.default.attributesOfItem(atPath: url.path),
              let size = attrs[.size] as? Int else {
            return nil
        }
        return size
    }

    private func adoptCachedDMGIfValid(
        destination: URL,
        release: GitHubRelease,
        notify: Bool
    ) async -> Bool {
        guard let size = fileSize(at: destination),
              release.assetSize <= 0 || size == release.assetSize else {
            return false
        }
        if let shaURL = release.sha256URL {
            do {
                let expected = try await fetchExpectedSHA256(from: shaURL)
                let verified = await Task.detached(priority: .utility) {
                    Self.verifySHA256(file: destination, expectedHex: expected)
                }.value
                if !verified {
                    UpdateLogger.log("cached DMG failed SHA256 check for v\(release.version)")
                    try? FileManager.default.removeItem(at: destination)
                    return false
                }
            } catch {
                UpdateLogger.log("cached DMG SHA256 fetch failed: \(error.localizedDescription)")
                return false
            }
        }
        cancelActiveDownload()
        setState(.readyToInstall(localDMG: destination, release: release))
        if notify {
            UpdateNotifier.notifyDownloadReady(version: release.version)
        }
        return true
    }

    private func cancelActiveDownload() {
        downloadTask?.cancel()
        downloadTask = nil
        activeDownloader?.cancel()
        activeDownloader = nil
        activeDownloadRelease = nil
    }

    nonisolated private static func verifySHA256(file: URL, expectedHex: String) -> Bool {
        guard let handle = try? FileHandle(forReadingFrom: file) else { return false }
        defer { try? handle.close() }
        var hasher = SHA256()
        while true {
            let chunk = handle.readData(ofLength: 1_048_576)
            if chunk.isEmpty { break }
            hasher.update(data: chunk)
        }
        let digest = hasher.finalize().map { String(format: "%02x", $0) }.joined()
        return digest == expectedHex.lowercased()
    }

    func startPolling(interval: TimeInterval = 3600) {
        pollTask?.cancel()
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                await self?.checkOnce(force: false)
                try? await Task.sleep(for: .seconds(interval))
            }
        }
    }

    func stopPolling() {
        pollTask?.cancel()
        pollTask = nil
    }

    func restartPollingIfNeeded(interval: TimeInterval = 3600) {
        if AppConfig.load().checkForUpdatesAutomatically {
            startPolling(interval: interval)
        } else {
            stopPolling()
        }
    }

    func checkOnce(force: Bool = true) async {
        if !force && !AppConfig.load().checkForUpdatesAutomatically {
            return
        }
        if case .installing = state {
            return
        }

        await reconcileDownloadWithDisk()
        if case .readyToInstall(_, let release) = state {
            if force {
                presentUpdateAlreadyCachedAlert(version: release.version)
            }
            return
        }

        let preservedReady: (URL, GitHubRelease)? = {
            if case .readyToInstall(let dmg, let release) = state {
                return (dmg, release)
            }
            return nil
        }()
        setState(.checking)
        guard let release = await checker.latestRelease(userAgent: userAgent) else {
            if force {
                setState(.failed("Unable to check for updates"))
                presentCheckFailedAlert("Could not reach GitHub to check for updates. Check your network and try again.")
            } else if let preservedReady {
                setState(.readyToInstall(localDMG: preservedReady.0, release: preservedReady.1))
            } else {
                setState(.idle)
            }
            return
        }
        if release.version.compare(currentVersion, options: .numeric) != .orderedDescending {
            if let preservedReady {
                setState(.readyToInstall(localDMG: preservedReady.0, release: preservedReady.1))
            } else {
                setState(.idle)
                if force {
                    presentUpToDateAlert()
                }
            }
            return
        }
        await reconcileDownloadWithDisk()
        if case .readyToInstall(_, let cached) = state {
            if force {
                presentUpdateAlreadyCachedAlert(version: cached.version)
            }
            return
        }
        if let preservedReady, preservedReady.1.version == release.version {
            setState(.readyToInstall(localDMG: preservedReady.0, release: preservedReady.1))
            return
        }
        setState(.available(release))
        if force {
            presentUpdateAvailableAlert(release: release)
        }
    }

    func downloadUpdate(release: GitHubRelease) {
        cancelActiveDownload()
        downloadTask = Task { await performDownload(release: release) }
    }

    private func performDownload(release: GitHubRelease) async {
        guard !Task.isCancelled else { return }
        guard release.hasDMGAsset else {
            openDownloadInBrowser(for: release)
            return
        }
        let destination = cachedDMGURL(for: release)
        if await adoptCachedDMGIfValid(destination: destination, release: release, notify: true) {
            UpdateLogger.log("download skipped — valid cache for v\(release.version)")
            presentDownloadReadyAlert(version: release.version)
            return
        }

        UpdateLogger.log("download started v\(release.version) from \(release.downloadURL.absoluteString)")
        activeDownloadRelease = release
        setState(.downloading(progress: 0))
        do {
            try FileManager.default.createDirectory(at: cacheDirectory, withIntermediateDirectories: true)
            try? FileManager.default.removeItem(at: destination)
            let downloader = UpdateDownloader { fraction in
                Task { @MainActor in
                    UpdateController.shared.applyDownloadProgress(fraction)
                }
            }
            activeDownloader = downloader
            let timeoutSeconds = Self.downloadTimeoutSeconds
            let (tmp, response) = try await withThrowingTaskGroup(of: (URL, URLResponse).self) { group in
                group.addTask {
                    try await downloader.download(from: release.downloadURL)
                }
                group.addTask {
                    try await Task.sleep(nanoseconds: timeoutSeconds * 1_000_000_000)
                    throw UpdateError.downloadFailed("Download timed out after \(timeoutSeconds / 60) minutes")
                }
                guard let result = try await group.next() else {
                    throw UpdateError.downloadFailed("Download ended without a result")
                }
                group.cancelAll()
                return result
            }
            guard !Task.isCancelled else { return }
            activeDownloader = nil
            defer { try? FileManager.default.removeItem(at: tmp) }
            if let http = response as? HTTPURLResponse, http.statusCode != 200 {
                throw UpdateError.downloadFailed("HTTP \(http.statusCode)")
            }
            if release.assetSize > 0 {
                let size = fileSize(at: tmp) ?? 0
                if size != release.assetSize {
                    throw UpdateError.downloadFailed("Size mismatch (expected \(release.assetSize), got \(size))")
                }
            }
            try FileManager.default.moveItem(at: tmp, to: destination)
            if let shaURL = release.sha256URL {
                let expected = try await fetchExpectedSHA256(from: shaURL)
                let verified = await Task.detached(priority: .utility) {
                    Self.verifySHA256(file: destination, expectedHex: expected)
                }.value
                if !verified {
                    try? FileManager.default.removeItem(at: destination)
                    throw UpdateError.verificationFailed("SHA256 mismatch")
                }
            }
            activeDownloadRelease = nil
            UpdateLogger.log("download complete v\(release.version) at \(destination.path)")
            setState(.readyToInstall(localDMG: destination, release: release))
            UpdateNotifier.notifyDownloadReady(version: release.version)
            presentDownloadReadyAlert(version: release.version)
        } catch is CancellationError {
            UpdateLogger.log("download cancelled v\(release.version)")
        } catch let error as UpdateError {
            activeDownloadRelease = nil
            activeDownloader = nil
            UpdateLogger.log("download failed: \(error.localizedDescription)")
            if await adoptCachedDMGIfValid(destination: destination, release: release, notify: false) {
                UpdateLogger.log("recovered to readyToInstall after download error")
                presentDownloadReadyAlert(version: release.version)
            } else {
                setState(.failed(error.localizedDescription))
            }
        } catch {
            activeDownloadRelease = nil
            activeDownloader = nil
            UpdateLogger.log("download failed: \(error.localizedDescription)")
            if await adoptCachedDMGIfValid(destination: destination, release: release, notify: false) {
                UpdateLogger.log("recovered to readyToInstall after download error")
                presentDownloadReadyAlert(version: release.version)
            } else {
                setState(.failed(error.localizedDescription))
            }
        }
    }

    private func presentDownloadReadyAlert(version: String) {
        NSApp.activate(ignoringOtherApps: true)
        let readyAlert = NSAlert()
        readyAlert.messageText = "Update v\(version) ready"
        readyAlert.informativeText = "Open the menubar popover and choose Install, or use Settings → Install and Quit."
        readyAlert.addButton(withTitle: "OK")
        readyAlert.runModal()
    }

    private func presentUpToDateAlert() {
        NSApp.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.messageText = "You're up to date"
        alert.informativeText = "\(AppBranding.displayName) v\(currentVersion) is the latest release available from GitHub."
        alert.addButton(withTitle: "OK")
        alert.runModal()
    }

    private func presentCheckFailedAlert(_ message: String) {
        NSApp.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.messageText = "Update check failed"
        alert.informativeText = message
        alert.alertStyle = .warning
        alert.addButton(withTitle: "OK")
        alert.runModal()
    }

    private func presentUpdateAvailableAlert(release: GitHubRelease) {
        NSApp.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.messageText = "Update available"
        alert.informativeText = "Version \(release.version) is available. Use Download in the menubar popover or Settings → Updates."
        alert.addButton(withTitle: "OK")
        alert.runModal()
    }

    private func presentUpdateAlreadyCachedAlert(version: String) {
        NSApp.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.messageText = "Update v\(version) ready to install"
        alert.informativeText = "The update is already downloaded. Choose Install in the menubar popover or Settings → Install and Quit."
        alert.addButton(withTitle: "OK")
        alert.runModal()
    }

    func installUpdate(release: GitHubRelease, localDMG: URL) async {
        guard InstallLocation.canAutoInstall() else {
            if let url = release.downloadURL as URL? {
                NSWorkspace.shared.open(url)
            }
            return
        }
        guard let installPath = InstallLocation.applicationsInstallPath() else { return }

        NSApp.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.messageText = "Install update v\(release.version)?"
        alert.informativeText = "The agent will stop and \(AppBranding.displayName) will quit while the app in Applications is replaced."
        alert.addButton(withTitle: "Install and Quit")
        alert.addButton(withTitle: "Install Later")
        guard alert.runModal() == .alertFirstButtonReturn else {
            UpdateLogger.log("install deferred v\(release.version) by user")
            return
        }

        UpdateLogger.log("install started v\(release.version) dmg=\(localDMG.path)")
        setState(.installing)
        if let server {
            await server.stop()
        }

        guard let script = Bundle.main.url(
            forResource: "macos-app-install",
            withExtension: "sh",
            subdirectory: "Scripts"
        ) else {
            let message = "Installer script missing from app bundle"
            UpdateLogger.log("install failed: \(message)")
            setState(.failed(message))
            presentCheckFailedAlert(message)
            return
        }

        let logsDir = AppConfig.appSupportURL().appendingPathComponent("logs", isDirectory: true)
        try? FileManager.default.createDirectory(at: logsDir, withIntermediateDirectories: true)
        let installLogURL = logsDir.appendingPathComponent("install.log")
        if !FileManager.default.fileExists(atPath: installLogURL.path) {
            FileManager.default.createFile(atPath: installLogURL.path, contents: nil)
        }

        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/bash")
        process.arguments = [
            script.path,
            "--in-app-update",
            "--wait-for-pid", "\(ProcessInfo.processInfo.processIdentifier)",
            "--dmg", localDMG.path,
            "--install-path", installPath.path,
            "--cache-cleanup", cacheDirectory.path,
            "--log-file", installLogURL.path,
        ]
        if let logHandle = try? FileHandle(forWritingTo: installLogURL) {
            try? logHandle.seekToEnd()
            let header = "\n--- install v\(release.version) \(ISO8601DateFormatter().string(from: Date())) ---\n"
            if let data = header.data(using: .utf8) {
                try? logHandle.write(contentsOf: data)
            }
            process.standardOutput = logHandle
            process.standardError = logHandle
        }
        do {
            try process.run()
        } catch {
            let message = "Failed to start installer: \(error.localizedDescription)"
            UpdateLogger.log("install failed: \(message)")
            setState(.failed(message))
            presentCheckFailedAlert(message)
            return
        }
        NSApp.terminate(nil)
    }

    func installFromReadyState() async {
        guard case .readyToInstall(let dmg, let release) = state else { return }
        await installUpdate(release: release, localDMG: dmg)
    }

    func openReleaseNotes(for release: GitHubRelease) {
        NSWorkspace.shared.open(release.releaseNotesURL)
    }

    func openDownloadInBrowser(for release: GitHubRelease) {
        NSWorkspace.shared.open(release.downloadURL)
    }

    var availableRelease: GitHubRelease? {
        switch state {
        case .available(let release), .readyToInstall(_, let release):
            return release
        default:
            return nil
        }
    }

    var statusLabel: String? {
        switch state {
        case .idle:
            return nil
        case .checking:
            return "Checking for updates…"
        case .available(let release):
            if release.hasDMGAsset {
                return "Update available (v\(release.version))"
            }
            return "Update available (v\(release.version)) — download manually"
        case .downloading(let progress):
            if let progress {
                return String(format: "Downloading update… %.0f%%", progress * 100)
            }
            return "Downloading update…"
        case .readyToInstall(_, let release):
            return "Ready to install v\(release.version)"
        case .installing:
            return "Installing update…"
        case .failed(let message):
            return "Update failed: \(message)"
        }
    }

    func applyDownloadProgress(_ fraction: Double) {
        guard activeDownloadRelease != nil else { return }
        setState(.downloading(progress: fraction))
    }

    private func setState(_ newState: UpdateState) {
        let skipLog: Bool = {
            if case .downloading = state, case .downloading = newState { return true }
            return false
        }()
        state = newState
        if !skipLog {
            logStateTransition(newState)
        }
        NotificationCenter.default.post(name: .netllmUpdateStateDidChange, object: self)
    }

    private func logStateTransition(_ newState: UpdateState) {
        switch newState {
        case .idle:
            UpdateLogger.log("state idle")
        case .checking:
            UpdateLogger.log("state checking")
        case .available(let release):
            UpdateLogger.log("state available v\(release.version)")
        case .downloading(let progress):
            if let progress {
                UpdateLogger.log(String(format: "state downloading %.0f%%", progress * 100))
            } else {
                UpdateLogger.log("state downloading")
            }
        case .readyToInstall(_, let release):
            UpdateLogger.log("state readyToInstall v\(release.version)")
        case .installing:
            UpdateLogger.log("state installing")
        case .failed(let message):
            UpdateLogger.log("state failed: \(message)")
        }
    }

    private func fetchExpectedSHA256(from url: URL) async throws -> String {
        var request = URLRequest(url: url)
        request.setValue(userAgent, forHTTPHeaderField: "User-Agent")
        let (data, response) = try await URLSession.shared.data(for: request)
        guard (response as? HTTPURLResponse)?.statusCode == 200 else {
            throw UpdateError.verificationFailed("Unable to fetch checksum")
        }
        let text = String(decoding: data, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
        return text.split(separator: " ").first.map(String.init)?.lowercased() ?? text.lowercased()
    }

}

enum UpdateError: LocalizedError {
    case downloadFailed(String)
    case verificationFailed(String)

    var errorDescription: String? {
        switch self {
        case .downloadFailed(let detail), .verificationFailed(let detail):
            return detail
        }
    }
}

final class ReleasesChecker: Sendable {
    let repo: String
    let preferredAssetName: String

    init(repo: String = "matthewdcage/llm-swarm-router", preferredAssetName: String = "llm-swarm-router.dmg") {
        self.repo = repo
        self.preferredAssetName = preferredAssetName
    }

    func latestRelease(userAgent: String) async -> GitHubRelease? {
        guard let url = URL(string: "https://api.github.com/repos/\(repo)/releases/latest") else {
            return nil
        }
        var request = URLRequest(url: url)
        request.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
        request.setValue(userAgent, forHTTPHeaderField: "User-Agent")
        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard (response as? HTTPURLResponse)?.statusCode == 200 else { return nil }
            guard let json = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
            if json["prerelease"] as? Bool == true { return nil }
            guard let tag = json["tag_name"] as? String,
                  let assets = json["assets"] as? [[String: Any]],
                  let htmlURLString = json["html_url"] as? String,
                  let htmlURL = URL(string: htmlURLString) else { return nil }
            let version = tag.hasPrefix("v") ? String(tag.dropFirst()) : tag

            var selected: (name: String, url: URL, size: Int)?
            for asset in assets {
                guard let name = asset["name"] as? String,
                      let browser = asset["browser_download_url"] as? String,
                      let dl = URL(string: browser) else { continue }
                let size = asset["size"] as? Int ?? 0
                if name == preferredAssetName {
                    selected = (name, dl, size)
                    break
                }
                if selected == nil, name.hasSuffix(".dmg") {
                    selected = (name, dl, size)
                }
            }
            if let picked = selected {
                let shaURL = assets.compactMap { asset -> URL? in
                    guard let name = asset["name"] as? String,
                          name == "\(picked.name).sha256",
                          let browser = asset["browser_download_url"] as? String else { return nil }
                    return URL(string: browser)
                }.first

                return GitHubRelease(
                    version: version,
                    downloadURL: picked.url,
                    assetName: picked.name,
                    assetSize: picked.size,
                    sha256URL: shaURL,
                    releaseNotesURL: htmlURL,
                    hasDMGAsset: true
                )
            }

            return GitHubRelease(
                version: version,
                downloadURL: htmlURL,
                assetName: "",
                assetSize: 0,
                sha256URL: nil,
                releaseNotesURL: htmlURL,
                hasDMGAsset: false
            )
        } catch {
            return nil
        }
    }
}
