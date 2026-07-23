import SwiftUI

@main
struct NetllmMacApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var appDelegate

    init() {
        let version = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "?"
        AppLogger.log("NetllmMacApp.init (v\(version) pid=\(ProcessInfo.processInfo.processIdentifier))")
    }

    var body: some Scene {
        Settings {
            EmptyView()
        }
    }
}
