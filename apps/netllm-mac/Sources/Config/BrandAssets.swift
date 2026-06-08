import AppKit
import SwiftUI

enum BrandAssets {
    private static let brandSubdirectory = "Brand"
    private static let menubarPointSize: CGFloat = 18

    // MARK: - Menubar (macOS menu bar light/dark)

    static func menubarIcon(for appearance: NSAppearance) -> NSImage? {
        // Template icons are tinted by AppKit (black → white on dark menu bar).
        // NSStatusItem.button.effectiveAppearance is often .aqua even on a dark bar,
        // so manual light/dark PNG swapping is unreliable.
        if let template = templateMenubarIcon() {
            return template
        }
        let darkMenuBar = isDarkMenuBar(appearance)
        let base = darkMenuBar ? "MenubarIconDark" : "MenubarIconLight"
        return loadMenubarImage(baseName: base, template: false)
    }

    static func menubarIcon() -> NSImage? {
        menubarIcon(for: NSApp.effectiveAppearance)
    }

    // MARK: - In-app UI (settings, welcome — transparent assets, no solid backgrounds)

    static func uiLogo(forDarkMode: Bool) -> NSImage? {
        if forDarkMode {
            return rasterImage(named: "llm-swam-router-icon-white")
        }
        return rasterImage(named: "llm-swam-router-icon")
    }

    static func applicationIcon() -> NSImage? {
        if let url = Bundle.main.url(forResource: "AppIcon", withExtension: "icns"),
           let icon = NSImage(contentsOf: url) {
            return icon
        }
        return rasterImage(named: "llm-swam-router-icon-black-bg")
    }

    static func aboutIcon() -> NSImage? {
        uiLogo(forDarkMode: isDarkAqua(NSApp.effectiveAppearance))
            ?? applicationIcon()
    }

    // MARK: - Private

    private static func isDarkAqua(_ appearance: NSAppearance) -> Bool {
        appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
    }

    /// Menu bar appearance is not always reflected on NSStatusItem buttons; combine signals.
    private static func isDarkMenuBar(_ appearance: NSAppearance) -> Bool {
        if isDarkAqua(appearance) { return true }
        if isDarkAqua(NSApp.effectiveAppearance) { return true }
        if UserDefaults.standard.string(forKey: "AppleInterfaceStyle") == "Dark" { return true }
        return false
    }

    private static func loadMenubarImage(baseName: String, template: Bool) -> NSImage? {
        let image = NSImage(size: NSSize(width: menubarPointSize, height: menubarPointSize))
        var added = false
        for suffix in ["", "@2x"] {
            guard let url = brandURL(named: "\(baseName)\(suffix).png"),
                  FileManager.default.fileExists(atPath: url.path),
                  let source = NSImage(contentsOf: url) else { continue }
            let scale: CGFloat = suffix.isEmpty ? 1 : 2
            source.size = NSSize(width: menubarPointSize * scale, height: menubarPointSize * scale)
            for rep in source.representations {
                image.addRepresentation(rep)
                added = true
            }
        }
        guard added else { return nil }
        image.isTemplate = template
        return image
    }

    private static func templateMenubarIcon() -> NSImage? {
        if let image = loadMenubarImage(baseName: "MenubarIconLight", template: true) {
            return image
        }
        let image = NSImage(size: NSSize(width: menubarPointSize, height: menubarPointSize))
        var added = false
        for name in ["MenubarIcon", "MenubarIcon@2x"] {
            guard let url = brandURL(named: "\(name).png"),
                  FileManager.default.fileExists(atPath: url.path),
                  let source = NSImage(contentsOf: url) else { continue }
            source.size = NSSize(width: menubarPointSize, height: menubarPointSize)
            for rep in source.representations {
                image.addRepresentation(rep)
                added = true
            }
        }
        guard added else { return nil }
        image.isTemplate = true
        return image
    }

    private static func brandURL(named filename: String) -> URL? {
        Bundle.main.resourceURL?
            .appendingPathComponent(brandSubdirectory)
            .appendingPathComponent(filename)
    }

    private static func rasterImage(named: String) -> NSImage? {
        guard let url = brandURL(named: "\(named).png"),
              FileManager.default.fileExists(atPath: url.path) else { return nil }
        return NSImage(contentsOf: url)
    }
}

/// SwiftUI logo that tracks light/dark window appearance (transparent PNG, no tile background).
struct BrandImageView: View {
    @Environment(\.colorScheme) private var colorScheme
    var size: CGFloat = 40

    var body: some View {
        Group {
            if let image = BrandAssets.uiLogo(forDarkMode: colorScheme == .dark) {
                Image(nsImage: image)
                    .resizable()
                    .interpolation(.high)
                    .aspectRatio(contentMode: .fit)
                    .background(Color.clear)
            } else {
                Image(systemName: "point.3.connected.trianglepath.dotted")
                    .font(.system(size: size * 0.55))
                    .foregroundStyle(.secondary)
            }
        }
        .frame(width: size, height: size)
    }
}
