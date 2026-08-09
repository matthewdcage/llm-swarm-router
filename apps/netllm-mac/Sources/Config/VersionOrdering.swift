import Foundation

/// Ordering for netllm version strings, matching
/// `netllm_core.update.compare_versions` exactly.
///
/// The app and the agent both have to answer "is this peer older than me".
/// Two independent implementations with no corpus in common is how they end up
/// disagreeing, so both consume `tests/contract/version-ordering.json`
/// (see VersionOrderingTests.swift and tests/test_version_ordering.py).
///
/// The naive implementation — scrape every digit run and compare the lists —
/// reads `0.5.0rc1` as `[0, 5, 0, 1]`: newer than `0.5.0` and equal to the
/// real build `0.5.0.1`. That is the defect this type exists to not repeat.
enum VersionOrdering {
    /// Prerelease ranks, all below `finalRank`. The Python side asserts this
    /// roster matches `_PRERELEASE_RANK`; keep the literal spelling
    /// (`"label": rank`) because that assertion parses this file.
    static let prereleaseRank: [String: Int] = [
        "dev": -4,
        "alpha": -3,
        "a": -3,
        "beta": -2,
        "b": -2,
        "rc": -1,
        "c": -1,
        "pre": -1,
        "preview": -1,
    ]
    static let finalRank = 0

    struct Key {
        var release: [Int]
        var rank: Int
        var number: Int
    }

    /// Longest alternatives first so "alpha" is not eaten by "a". An
    /// unrecognised trailing tag (`0.2.2.1.ci`, `+build`) is ignored rather
    /// than reordering anything.
    private static let pattern =
        "^\\s*[vV]?([0-9]+(?:\\.[0-9]+)*)"
        + "(?:[-._]?(preview|alpha|beta|dev|pre|rc|a|b|c)(?![A-Za-z])[-._]?([0-9]+)?)?"

    static func key(_ value: String) -> Key {
        guard
            let regex = try? NSRegularExpression(
                pattern: pattern, options: [.caseInsensitive]),
            let match = regex.firstMatch(
                in: value,
                range: NSRange(value.startIndex..<value.endIndex, in: value))
        else {
            return Key(release: [0], rank: finalRank, number: 0)
        }

        func group(_ index: Int) -> String? {
            let range = match.range(at: index)
            guard range.location != NSNotFound, let r = Range(range, in: value)
            else { return nil }
            return String(value[r])
        }

        let release = (group(1) ?? "0").split(separator: ".").map { Int($0) ?? 0 }
        guard let label = group(2)?.lowercased(), let rank = prereleaseRank[label] else {
            return Key(release: release, rank: finalRank, number: 0)
        }
        return Key(release: release, rank: rank, number: Int(group(3) ?? "0") ?? 0)
    }

    /// -1 if `current` is older, 0 if the same release, 1 if newer.
    static func compare(_ current: String, _ latest: String) -> Int {
        let left = key(current)
        let right = key(latest)
        let width = max(left.release.count, right.release.count)
        for index in 0..<width {
            let a = index < left.release.count ? left.release[index] : 0
            let b = index < right.release.count ? right.release[index] : 0
            if a != b { return a < b ? -1 : 1 }
        }
        if left.rank != right.rank { return left.rank < right.rank ? -1 : 1 }
        if left.number != right.number { return left.number < right.number ? -1 : 1 }
        return 0
    }
}
