import XCTest
@testable import NetllmMac

final class CompactCountFormatterTests: XCTestCase {
    func testCompactMillions() {
        XCTAssertEqual(CompactCountFormatter.format(147_800_000), "147.8M")
    }

    func testCompactThousands() {
        XCTAssertEqual(CompactCountFormatter.format(25_600), "25.6K")
    }

    func testTooltipUsesFullInteger() {
        XCTAssertEqual(CompactCountFormatter.tooltip(25600), "25600")
    }

    func testTpsFormatting() {
        XCTAssertEqual(CompactCountFormatter.formatTps(41.2), "41.2 tok/s")
    }
}

final class TelemetryPayloadDecodeTests: XCTestCase {
    func testDecodeFixture() throws {
        let json = """
        {"schema_version":1,"omlx":{"available":true,"live":{"prefill_tps":10,"generation_tps":5}}}
        """.data(using: .utf8)!
        let obj = try JSONSerialization.jsonObject(with: json) as? [String: Any]
        let snap = TelemetrySnapshot(raw: obj ?? [:])
        XCTAssertTrue(snap.omlxAvailable)
        XCTAssertEqual(snap.livePP, 10)
        XCTAssertEqual(snap.liveTG, 5)
    }
}

@MainActor
final class MenubarStatusTitleTests: XCTestCase {
    func testMenubarStatusTitleRunning() {
        let title = MenubarAppModel.menubarStatusTitle(state: .running(pid: 42), port: 11400)
        XCTAssertTrue(title.contains("Agent running"))
        XCTAssertTrue(title.contains("11400"))
    }

    func testMenubarStatusTitleStopped() {
        XCTAssertEqual(
            MenubarAppModel.menubarStatusTitle(state: .stopped, port: 11400),
            "Agent stopped"
        )
    }

    func testMenubarStatusTitleStarting() {
        XCTAssertTrue(
            MenubarAppModel.menubarStatusTitle(state: .starting, port: 11400)
                .contains("Agent starting")
        )
    }

    func testMenubarStatusTitleWithPeers() {
        let title = MenubarAppModel.menubarStatusTitle(
            state: .running(pid: 1),
            port: 11400,
            peerCount: 2
        )
        XCTAssertTrue(title.contains("2 peers"))
    }
}

@MainActor
final class AgentSupervisorStatusLabelTests: XCTestCase {
    func testSettingsStatusLabelRunning() {
        XCTAssertEqual(ServerProcess.State.running(pid: 42).settingsStatusLabel, "Running")
    }

    func testSettingsStatusLabelFailed() {
        XCTAssertEqual(
            ServerProcess.State.failed(message: "port in use").settingsStatusLabel,
            "Failed — port in use"
        )
    }
}

/// The first Swift test to reference the Keychain / cloud-provider axis at
/// all. It covers the one silent load-bearing hardcode the audit found: the
/// env vars PythonRuntime exports for stored cloud API keys used to be a
/// closed table of five, so a sixth provider stored a key that never reached
/// the agent (docs/extending/PROGRAM.md §2, item 0b.1).
final class CloudKeyEnvTests: XCTestCase {
    func testEnvVarDerivesFromProviderID() {
        XCTAssertEqual(
            KeychainStore.CloudKeyEnv.defaultEnvVar(for: "moonshot"),
            "MOONSHOT_API_KEY"
        )
        XCTAssertEqual(
            KeychainStore.CloudKeyEnv.defaultEnvVar(for: "dashscope"),
            "DASHSCOPE_API_KEY"
        )
    }

    func testEveryBootstrapProviderIsInjectedWithNoRegistryFetch() {
        let pairs = KeychainStore.CloudKeyEnv.injectionPairs(remembered: [:])
        let ids = KeychainStore.CloudKeyEnv.bootstrapProviderIDs
        XCTAssertEqual(pairs.count, ids.count)
        for id in ids {
            let account = KeychainStore.accountForCloudProvider(id)
            XCTAssertTrue(
                pairs.contains { $0.account == account },
                "\(id) would store a key that is never exported"
            )
        }
    }

    func testProviderKnownOnlyToTheRegistryIsInjected() {
        // The regression this whole change exists to prevent: an id this
        // binary has never heard of, arriving over the wire.
        let pairs = KeychainStore.CloudKeyEnv.injectionPairs(
            remembered: ["dashscope": "DASHSCOPE_API_KEY"]
        )
        XCTAssertTrue(
            pairs.contains {
                $0.account == "dashscope_api_key" && $0.envVar == "DASHSCOPE_API_KEY"
            }
        )
        // …without dropping the ones it does know.
        XCTAssertTrue(pairs.contains { $0.envVar == "ANTHROPIC_API_KEY" })
    }

    func testWireValueBeatsTheDerivedDefault() {
        let pairs = KeychainStore.CloudKeyEnv.injectionPairs(
            remembered: ["zai": "ZHIPU_API_KEY"]
        )
        XCTAssertEqual(pairs.first { $0.account == "zai_api_key" }?.envVar, "ZHIPU_API_KEY")
    }

    func testResolvedAPIKeyEnvPrefersTheServedValue() {
        let served = CloudProviderInfo(
            id: "zai",
            displayName: "Z.ai",
            notes: "",
            regions: ["api"],
            keychainAccount: "zai_api_key",
            apiKeyEnv: "ZHIPU_API_KEY"
        )
        XCTAssertEqual(served.resolvedAPIKeyEnv, "ZHIPU_API_KEY")

        let offline = CloudProviderInfo(
            id: "zai",
            displayName: "Z.ai",
            notes: "",
            regions: ["api"],
            keychainAccount: "zai_api_key"
        )
        XCTAssertEqual(offline.resolvedAPIKeyEnv, "ZAI_API_KEY")
    }
}
