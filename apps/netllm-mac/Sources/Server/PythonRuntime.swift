import Foundation

struct PythonRuntime: Sendable {
    let executable: URL
    let pythonHome: URL
    let pythonPath: String
    let bundleRoot: URL

    init() {
        if let env = ProcessInfo.processInfo.environment["NETLLM_BUNDLE_PATH"],
           !env.isEmpty {
            let candidate = URL(fileURLWithPath: env)
            let bundledCLI = candidate.appendingPathComponent("Contents/MacOS/netllm-cli")
            if FileManager.default.isExecutableFile(atPath: bundledCLI.path) {
                bundleRoot = candidate
            } else {
                bundleRoot = Bundle.main.bundleURL
            }
        } else {
            // bundleURL is the .app root (…/llm-swarm-router.app), not Contents/.
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
        let bundledPython = pythonHome.appendingPathComponent("bin/python3")
        if FileManager.default.isExecutableFile(atPath: bundledPython.path) {
            env["PYTHONHOME"] = pythonHome.path
            env["PYTHONPATH"] = pythonPath
        } else {
            env.removeValue(forKey: "PYTHONHOME")
            env.removeValue(forKey: "PYTHONPATH")
        }
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["NETLLM_SUPERVISED"] = "menubar"
        env["NETLLM_BUNDLE_PATH"] = bundleRoot.path
        let shimBin = ShellEnvWriter.shimPath().deletingLastPathComponent().path
        env["NETLLM_CLI_SHIM"] = ShellEnvWriter.shimPath().path
        env["PATH"] = "\(shimBin):/opt/homebrew/bin:/usr/local/bin:" + (env["PATH"] ?? "")
        injectCloudAPIKeys(into: &env)
        return env
    }

    private func injectCloudAPIKeys(into env: inout [String: String]) {
        if env["ANTHROPIC_API_KEY"]?.isEmpty != false,
           let key = KeychainStore.load(account: KeychainStore.Account.anthropicAPIKey),
           !key.isEmpty {
            env["ANTHROPIC_API_KEY"] = key
        }
        if env["OPENAI_API_KEY"]?.isEmpty != false,
           let key = KeychainStore.load(account: KeychainStore.Account.openaiAPIKey),
           !key.isEmpty {
            env["OPENAI_API_KEY"] = key
        }
    }
}
