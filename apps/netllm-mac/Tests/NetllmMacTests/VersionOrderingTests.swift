import XCTest

@testable import NetllmMac

/// Drives `VersionOrdering` from `tests/contract/version-ordering.json` — the
/// same corpus `tests/test_version_ordering.py` drives the Python comparator
/// from. Neither side may add a case without the other answering it.
///
/// The corpus is located from `#filePath` rather than bundled as an SPM
/// resource on purpose: a copied resource is a second copy, which is the thing
/// this test exists to prevent.
final class VersionOrderingTests: XCTestCase {
    struct Case: Decodable {
        let left: String
        let right: String
        let expect: Int
        let why: String
    }

    struct Corpus: Decodable {
        let cases: [Case]
    }

    static func corpusURL() -> URL {
        // .../apps/netllm-mac/Tests/NetllmMacTests/VersionOrderingTests.swift
        var url = URL(fileURLWithPath: #filePath)
        for _ in 0..<5 { url.deleteLastPathComponent() }
        return url.appendingPathComponent("tests/contract/version-ordering.json")
    }

    func loadCases() throws -> [Case] {
        let url = Self.corpusURL()
        let data = try Data(contentsOf: url)
        return try JSONDecoder().decode(Corpus.self, from: data).cases
    }

    func testCorpusIsReachableAndNonEmpty() throws {
        let cases = try loadCases()
        XCTAssertFalse(
            cases.isEmpty,
            "version-ordering.json carries no cases at \(Self.corpusURL().path)")
    }

    func testEveryCorpusCase() throws {
        for item in try loadCases() {
            let got = VersionOrdering.compare(item.left, item.right)
            XCTAssertEqual(
                got, item.expect,
                "compare(\"\(item.left)\", \"\(item.right)\") = \(got), "
                    + "corpus says \(item.expect): \(item.why)")
        }
    }

    func testOrderingIsAntisymmetric() throws {
        for item in try loadCases() {
            let forward = VersionOrdering.compare(item.left, item.right)
            let backward = VersionOrdering.compare(item.right, item.left)
            XCTAssertEqual(
                backward, -forward,
                "\"\(item.left)\" vs \"\(item.right)\" orders \(forward) one way "
                    + "and \(backward) the other")
        }
    }
}
