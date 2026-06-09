import Foundation
import UserNotifications

enum UpdateNotifier {
    static func requestAuthorizationIfNeeded() {
        UNUserNotificationCenter.current().getNotificationSettings { settings in
            guard settings.authorizationStatus == .notDetermined else { return }
            UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { _, _ in }
        }
    }

    static func notifyDownloadReady(version: String) {
        let content = UNMutableNotificationContent()
        content.title = "Update ready to install"
        content.body = "v\(version) downloaded. Open \(AppBranding.displayName) → Updates → Install Update."
        content.sound = .default
        let request = UNNotificationRequest(
            identifier: "netllm.update.ready.\(version)",
            content: content,
            trigger: nil
        )
        UNUserNotificationCenter.current().add(request)
    }
}
