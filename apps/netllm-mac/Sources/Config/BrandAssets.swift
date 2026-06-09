import AppKit
import SwiftUI

enum BrandAssets {
    private static let brandSubdirectory = "Brand"
    private static let menubarPointSize: CGFloat = 18

    // MARK: - Menubar (macOS menu bar light/dark)

    static func menubarIcon(for appearance: NSAppearance) -> NSImage? {
        _ = appearance
        return templateMenubarIcon()
    }

    static func menubarIcon() -> NSImage? {
        templateMenubarIcon()
    }

    // MARK: - In-app UI (settings, welcome — transparent assets, no solid backgrounds)

    static func uiLogo(forDarkMode: Bool) -> NSImage? {
        if forDarkMode {
            return rasterImage(named: "llm-swam-router-icon-white")
        }
        return rasterImage(named: "llm-swam-router-icon")
    }

    static func applicationIcon(for appearance: NSAppearance? = nil) -> NSImage? {
        let resolved = appearance ?? NSApp.effectiveAppearance
        if let logo = uiLogo(forDarkMode: isDarkAqua(resolved)) {
            return logo
        }
        if let url = Bundle.main.url(forResource: "AppIcon", withExtension: "icns"),
           let icon = NSImage(contentsOf: url) {
            return icon
        }
        return rasterImage(named: "llm-swam-router-icon")
    }

    static func aboutIcon() -> NSImage? {
        applicationIcon(for: NSApp.effectiveAppearance)
    }

    // MARK: - Private

    private static func isDarkAqua(_ appearance: NSAppearance) -> Bool {
        appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
    }

    private static func templateMenubarIcon() -> NSImage? {
        let image = NSImage(size: NSSize(width: menubarPointSize, height: menubarPointSize))
        var added = false
        for baseName in ["MenubarIconLight", "MenubarIcon"] {
            for suffix in ["", "@2x"] {
                guard let url = brandURL(named: "\(baseName)\(suffix).png"),
                      FileManager.default.fileExists(atPath: url.path),
                      let source = NSImage(contentsOf: url) else { continue }
                let scale: CGFloat = suffix.isEmpty ? 1 : 2
                source.size = NSSize(
                    width: menubarPointSize * scale,
                    height: menubarPointSize * scale
                )
                for rep in source.representations {
                    image.addRepresentation(rep)
                    added = true
                }
            }
            if added { break }
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
