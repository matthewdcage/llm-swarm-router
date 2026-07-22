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
