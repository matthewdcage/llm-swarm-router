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

    func pruneCacheOnLaunch() {
        pruneUpdateCache(keepVersion: nil)
        let items = (try? FileManager.default.contentsOfDirectory(
            at: cacheDirectory,
            includingPropertiesForKeys: nil
        )) ?? []
        for item in items {
            if item.pathExtension == "download" {
                try? FileManager.default.removeItem(at: item)
            }
        }
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
            }
            return
        }
        if let preservedReady, preservedReady.1.version == release.version {
            setState(.readyToInstall(localDMG: preservedReady.0, release: preservedReady.1))
            return
        }
        setState(.available(release))
    }

    func downloadUpdate(release: GitHubRelease) async {
        setState(.downloading(progress: nil))
        let destination = cacheDirectory.appendingPathComponent("\(preferredAssetName.replacingOccurrences(of: ".dmg", with: ""))-\(release.version).dmg")
        do {
            try FileManager.default.createDirectory(at: cacheDirectory, withIntermediateDirectories: true)
            try? FileManager.default.removeItem(at: destination)
            let (tmp, response) = try await URLSession.shared.download(from: release.downloadURL)
            defer { try? FileManager.default.removeItem(at: tmp) }
            if let http = response as? HTTPURLResponse, http.statusCode != 200 {
                throw UpdateError.downloadFailed("HTTP \(http.statusCode)")
            }
            if release.assetSize > 0 {
                let attrs = try FileManager.default.attributesOfItem(atPath: tmp.path)
                let size = attrs[.size] as? Int ?? 0
                if size != release.assetSize {
                    throw UpdateError.downloadFailed("Size mismatch (expected \(release.assetSize), got \(size))")
                }
            }
            try FileManager.default.moveItem(at: tmp, to: destination)
            if let shaURL = release.sha256URL {
                let expected = try await fetchExpectedSHA256(from: shaURL)
                if !verifySHA256(file: destination, expectedHex: expected) {
                    try? FileManager.default.removeItem(at: destination)
                    throw UpdateError.verificationFailed("SHA256 mismatch")
                }
            }
            setState(.readyToInstall(localDMG: destination, release: release))
        } catch let error as UpdateError {
            setState(.failed(error.localizedDescription))
        } catch {
            setState(.failed(error.localizedDescription))
        }
    }

    func installUpdate(release: GitHubRelease, localDMG: URL) async {
        guard InstallLocation.canAutoInstall() else {
            if let url = release.downloadURL as URL? {
                NSWorkspace.shared.open(url)
            }
            return
        }
        guard let installPath = InstallLocation.applicationsInstallPath() else { return }

        let alert = NSAlert()
        alert.messageText = "Install update v\(release.version)?"
        alert.informativeText = "The agent will stop and \(AppBranding.displayName) will quit while the app in Applications is replaced."
        alert.addButton(withTitle: "Install and Quit")
        alert.addButton(withTitle: "Cancel")
        guard alert.runModal() == .alertFirstButtonReturn else { return }

        setState(.installing)
        if let server {
            await server.stop()
        }

        guard let script = Bundle.main.url(
            forResource: "macos-app-install",
            withExtension: "sh",
            subdirectory: "Scripts"
        ) else {
            setState(.failed("Installer script missing from app bundle"))
            return
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
        ]
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        do {
            try process.run()
        } catch {
            setState(.failed("Failed to start installer: \(error.localizedDescription)"))
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
            return "Update available (v\(release.version))"
        case .downloading:
            return "Downloading update…"
        case .readyToInstall(_, let release):
            return "Ready to install v\(release.version)"
        case .installing:
            return "Installing update…"
        case .failed(let message):
            return "Update failed: \(message)"
        }
    }

    private func setState(_ newState: UpdateState) {
        state = newState
        NotificationCenter.default.post(name: .netllmUpdateStateDidChange, object: self)
    }

    private func pruneUpdateCache(keepVersion: String?) {
        guard FileManager.default.fileExists(atPath: cacheDirectory.path) else { return }
        for item in (try? FileManager.default.contentsOfDirectory(
            at: cacheDirectory,
            includingPropertiesForKeys: nil
        )) ?? [] {
            if item.pathExtension != "dmg" { continue }
            if let keepVersion, item.lastPathComponent.contains(keepVersion) { continue }
            try? FileManager.default.removeItem(at: item)
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

    private func verifySHA256(file: URL, expectedHex: String) -> Bool {
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
            guard let picked = selected else { return nil }

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
                releaseNotesURL: htmlURL
            )
        } catch {
            return nil
        }
    }
}
