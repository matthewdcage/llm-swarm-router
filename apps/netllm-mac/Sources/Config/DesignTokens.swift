import CoreGraphics
import SwiftUI

/// Shared visual tokens for the menubar app and web dashboard (see design-tokens.json).
enum DesignTokens {
    static let cornerRadius: CGFloat = 10
    static let cardPadding: CGFloat = 16
    static let popoverWidth: CGFloat = 340

    static let accentLight = Color(red: 0, green: 122 / 255, blue: 1)
    static let accentDark = Color(red: 10 / 255, green: 132 / 255, blue: 1)

    static var accent: Color {
        Color(nsColor: .controlAccentColor)
    }

    static let ok = Color(red: 52 / 255, green: 199 / 255, blue: 89 / 255)
    static let warn = Color(red: 1, green: 149 / 255, blue: 0)
    static let danger = Color(red: 1, green: 59 / 255, blue: 48 / 255)

    static func cardStroke(opacity: Double = 0.06) -> some ShapeStyle {
        Color.primary.opacity(opacity)
    }
}
