// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "netllm-mac",
    platforms: [.macOS(.v15)],
    targets: [
        .executableTarget(
            name: "NetllmMac",
            path: "Sources"
        ),
    ]
)
