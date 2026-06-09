import SwiftUI

@main
struct NetllmMacApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var appDelegate

    init() {
        let version = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "?"
        AppLogger.log("NetllmMacApp.init (v\(version) pid=\(ProcessInfo.processInfo.processIdentifier))")
    }

    var body: some Scene {
        MenuBarExtra {
            if MenubarAppModel.shared.isConfigured {
                MenubarPopoverView(model: MenubarAppModel.shared)
            } else {
                ProgressView("Starting…")
                    .padding(DesignTokens.cardPadding)
                    .frame(width: DesignTokens.popoverWidth)
            }
        } label: {
            MenubarStatusLabel(model: MenubarAppModel.shared)
        }
        .menuBarExtraStyle(.window)

        Settings {
            EmptyView()
        }
    }
}
