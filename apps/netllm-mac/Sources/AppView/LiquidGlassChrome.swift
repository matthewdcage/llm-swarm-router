import SwiftUI

enum LiquidGlassChrome {
    /// True when running on macOS 26+ (Tahoe); SDK may be newer than runtime.
    static var isRuntimeAvailable: Bool {
        ProcessInfo.processInfo.operatingSystemVersion.majorVersion >= 26
    }
}

struct NetllmCardChrome: ViewModifier {
    var cornerRadius: CGFloat = DesignTokens.cornerRadius

    func body(content: Content) -> some View {
        let shape = RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
        if #available(macOS 26.0, *), LiquidGlassChrome.isRuntimeAvailable {
            content
                .glassEffect(.regular, in: shape)
        } else {
            content
                .background(Color(nsColor: .controlBackgroundColor))
                .clipShape(shape)
                .overlay(shape.strokeBorder(DesignTokens.cardStroke(), lineWidth: 1))
        }
    }
}

extension View {
    func netllmCardChrome(cornerRadius: CGFloat = DesignTokens.cornerRadius) -> some View {
        modifier(NetllmCardChrome(cornerRadius: cornerRadius))
    }
}
