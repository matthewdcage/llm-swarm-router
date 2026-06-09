import Foundation
import ServiceManagement

enum LoginItemManager {
    enum LoginItemError: Error, LocalizedError {
        case registrationFailed(String)

        var errorDescription: String? {
            switch self {
            case .registrationFailed(let detail):
                "Could not update login item: \(detail)"
            }
        }
    }

    static var isRegistered: Bool {
        SMAppService.mainApp.status == .enabled
    }

    static func setRegistered(_ enabled: Bool) throws {
        do {
            if enabled {
                try SMAppService.mainApp.register()
            } else {
                try SMAppService.mainApp.unregister()
            }
        } catch {
            throw LoginItemError.registrationFailed(error.localizedDescription)
        }
    }

    /// When login-item APIs fail, auto_start_on_launch in config.toml still starts the agent.
    static var fallbackHint: String {
        "If launch at login is unavailable, enable auto-start in Settings → UI."
    }
}
