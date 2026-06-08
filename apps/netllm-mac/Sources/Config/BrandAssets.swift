import AppKit

enum BrandAssets {
    private static let brandSubdirectory = "Brand"
    private static let menubarPointSize: CGFloat = 18

    static func menubarIcon() -> NSImage? {
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

    static func applicationIcon() -> NSImage? {
        if let url = Bundle.main.url(forResource: "AppIcon", withExtension: "icns"),
           let icon = NSImage(contentsOf: url) {
            return icon
        }
        return rasterImage(named: "llm-swam-router-icon-black-bg")
    }

    static func aboutIcon() -> NSImage? {
        rasterImage(named: "llm-swam-router-icon") ?? applicationIcon()
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
