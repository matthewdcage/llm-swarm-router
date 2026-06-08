import Foundation

@MainActor
final class UpdateController {
    static let shared = UpdateController()
    private var task: Task<Void, Never>?
    private let checker = ReleasesChecker(repo: "matthewdcage/llm-swarm-router", assetSuffix: ".dmg")

    func startPolling(interval: TimeInterval = 3600) {
        task?.cancel()
        task = Task {
            while !Task.isCancelled {
                await checkOnce()
                try? await Task.sleep(for: .seconds(interval))
            }
        }
    }

    func checkOnce() async {
        guard let release = await checker.latestRelease() else { return }
        let current = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "0.0.0"
        if release.version.compare(current, options: .numeric) == .orderedDescending {
            NSLog("netllm update available: \(release.version)")
        }
    }
}

struct GitHubRelease: Sendable {
    let version: String
    let downloadURL: URL
}

final class ReleasesChecker: Sendable {
    let repo: String
    let assetSuffix: String

    init(repo: String, assetSuffix: String) {
        self.repo = repo
        self.assetSuffix = assetSuffix
    }

    func latestRelease() async -> GitHubRelease? {
        guard let url = URL(string: "https://api.github.com/repos/\(repo)/releases/latest") else {
            return nil
        }
        var request = URLRequest(url: url)
        request.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard (response as? HTTPURLResponse)?.statusCode == 200 else { return nil }
            guard let json = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let tag = json["tag_name"] as? String,
                  let assets = json["assets"] as? [[String: Any]] else { return nil }
            let version = tag.hasPrefix("v") ? String(tag.dropFirst()) : tag
            for asset in assets {
                guard let name = asset["name"] as? String,
                      name.hasSuffix(assetSuffix),
                      let browser = asset["browser_download_url"] as? String,
                      let dl = URL(string: browser) else { continue }
                return GitHubRelease(version: version, downloadURL: dl)
            }
        } catch {
            return nil
        }
        return nil
    }
}

final class AppUpdater: Sendable {
    func downloadAndStage(url: URL, to staging: URL) async throws {
        let (tmp, _) = try await URLSession.shared.download(from: url)
        try FileManager.default.createDirectory(at: staging.deletingLastPathComponent(), withIntermediateDirectories: true)
        try? FileManager.default.removeItem(at: staging)
        try FileManager.default.moveItem(at: tmp, to: staging)
    }
}
