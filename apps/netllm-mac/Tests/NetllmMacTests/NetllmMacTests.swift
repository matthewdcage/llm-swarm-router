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
