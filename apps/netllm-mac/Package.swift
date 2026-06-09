// swift-tools-version: 5.9
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
