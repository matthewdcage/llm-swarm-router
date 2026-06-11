import AppKit
import Foundation

enum JoinCommandExporter {
    static func format(listenURL: String, token: String) -> String {
        "netllm join \(listenURL) --token \(token)"
    }

    static func copyToPasteboard(_ command: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(command, forType: .string)
    }
}
