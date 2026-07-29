import Foundation

enum AgentHTTP {
    /// Builds an agent URL from a path that may include a query string.
    /// Do not use `URL.appendingPathComponent` for API paths — it encodes `?` as part of the path.
    static func url(base: URL, path: String) -> URL? {
        URL(string: path, relativeTo: base)?.absoluteURL
    }
}
