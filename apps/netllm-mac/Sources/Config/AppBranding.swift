import Foundation

/// User-facing product name for the macOS app (repo: llm-swarm-router).
/// Terminal CLI remains `netllm`.
enum AppBranding {
    static let displayName = "llm-swarm-router"
    static let tagline = "Mesh router for local LLM backends"
    static let settingsTitle = "\(displayName) Settings"
    static let aboutTitle = "About \(displayName)"
    static let welcomeTitle = "Welcome to \(displayName)"
    static let cliCommand = "netllm"
}
