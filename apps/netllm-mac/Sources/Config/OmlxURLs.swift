import Foundation

enum OmlxURLs {
    static func adminURL(from baseURL: String) -> String? {
        let trimmed = baseURL.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        var root = trimmed.hasSuffix("/") ? String(trimmed.dropLast()) : trimmed
        if root.hasSuffix("/v1") {
            root = String(root.dropLast(3))
        }
        return "\(root)/admin"
    }

    static func adminURL(from backends: [BackendSnapshot]) -> String? {
        for backend in backends where backend.provider == "omlx" && backend.health != "offline" {
            if let url = adminURL(from: backend.baseURL) {
                return url
            }
        }
        return nil
    }
}
