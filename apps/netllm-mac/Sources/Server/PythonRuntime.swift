import Foundation

struct PythonRuntime: Sendable {
    let executable: URL
    let pythonHome: URL
    let pythonPath: String
    let bundleRoot: URL

    init() {
        if let env = ProcessInfo.processInfo.environment["NETLLM_BUNDLE_PATH"],
           !env.isEmpty {
            bundleRoot = URL(fileURLWithPath: env)
        } else {
            // bundleURL is the .app root (…/netllm-mac.app), not Contents/.
            bundleRoot = Bundle.main.bundleURL
        }

        let pythonDir = bundleRoot.appendingPathComponent("Contents/Resources/Python")
        let runtime = pythonDir.appendingPathComponent("cpython-3.11")
        let framework = pythonDir
            .appendingPathComponent("framework-framework-netllm")
            .appendingPathComponent("lib/python3.11/site-packages")
        let packages = bundleRoot.appendingPathComponent("Contents/Resources/netllm_packages")
        var paths = [framework.path]
        if let entries = try? FileManager.default.contentsOfDirectory(
            at: packages,
            includingPropertiesForKeys: nil
        ) {
            for entry in entries {
                let src = entry.appendingPathComponent("src")
                if FileManager.default.fileExists(atPath: src.path) {
                    paths.append(src.path)
                }
            }
        }
        let meta = packages.appendingPathComponent("netllm")
        if FileManager.default.fileExists(atPath: meta.path) {
            paths.append(meta.path)
        }

        pythonHome = runtime
        executable = runtime.appendingPathComponent("bin/python3")
        pythonPath = paths.joined(separator: ":")
    }

    var bundleCLIPath: URL {
        bundleRoot.appendingPathComponent("Contents/MacOS/netllm-cli")
    }

    func makeEnvironment() -> [String: String] {
        var env = ProcessInfo.processInfo.environment
        env["PYTHONHOME"] = pythonHome.path
        env["PYTHONPATH"] = pythonPath
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["NETLLM_SUPERVISED"] = "menubar"
        env["NETLLM_BUNDLE_PATH"] = bundleRoot.path
        let shimBin = ShellEnvWriter.shimPath().deletingLastPathComponent().path
        env["NETLLM_CLI_SHIM"] = ShellEnvWriter.shimPath().path
        env["PATH"] = "\(shimBin):/opt/homebrew/bin:/usr/local/bin:" + (env["PATH"] ?? "")
        return env
    }
}
