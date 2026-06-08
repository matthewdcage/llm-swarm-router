import Foundation

enum InstallLocation {
    static let applicationNames = ["llm-swarm-router.app", "netllm-mac.app"]

    /// Returns the bundle URL when installed under `/Applications/` with a supported name.
    static func applicationsInstallPath() -> URL? {
        let bundleURL = Bundle.main.bundleURL
        let path = bundleURL.path
        guard path.hasPrefix("/Applications/") else { return nil }
        guard applicationNames.contains(bundleURL.lastPathComponent) else { return nil }
        return bundleURL
    }

    static func canAutoInstall() -> Bool {
        applicationsInstallPath() != nil
    }
}
