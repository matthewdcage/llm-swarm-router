import Foundation
import SwiftUI

/// A dynamic JSON value — backs config draft sections whose shape isn't
/// known at Swift compile time (docs/config-schema-rewrite-plan.md §3.4
/// Option A). `[String: JSONValue]` is itself `Codable` for free, so a
/// `NetllmConfigDocument` section typed this way round-trips through
/// `ConfigStore`'s JSON encode/decode unchanged.
enum JSONValue: Codable, Sendable, Equatable {
    case string(String)
    case number(Double)
    case bool(Bool)
    case array([JSONValue])
    case object([String: JSONValue])
    case null

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        // Order matters: Bool must be tried before Double (a JSON number
        // decoder would not accept true/false, but check bool first
        // anyway to keep this order-independent of any future decoder).
        if container.decodeNil() {
            self = .null
        } else if let value = try? container.decode(Bool.self) {
            self = .bool(value)
        } else if let value = try? container.decode(Double.self) {
            self = .number(value)
        } else if let value = try? container.decode(String.self) {
            self = .string(value)
        } else if let value = try? container.decode([JSONValue].self) {
            self = .array(value)
        } else if let value = try? container.decode([String: JSONValue].self) {
            self = .object(value)
        } else {
            throw DecodingError.dataCorruptedError(
                in: container,
                debugDescription: "Unsupported JSON value"
            )
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .string(let value): try container.encode(value)
        case .number(let value): try container.encode(value)
        case .bool(let value): try container.encode(value)
        case .array(let value): try container.encode(value)
        case .object(let value): try container.encode(value)
        case .null: try container.encodeNil()
        }
    }

    var stringValue: String? {
        if case .string(let value) = self { return value }
        return nil
    }

    var boolValue: Bool? {
        if case .bool(let value) = self { return value }
        return nil
    }

    var doubleValue: Double? {
        if case .number(let value) = self { return value }
        return nil
    }

    var arrayValue: [JSONValue]? {
        if case .array(let value) = self { return value }
        return nil
    }

    var objectValue: [String: JSONValue]? {
        if case .object(let value) = self { return value }
        return nil
    }

    var isNull: Bool {
        if case .null = self { return true }
        return false
    }

    static func strings(_ values: [String]) -> JSONValue {
        .array(values.map(JSONValue.string))
    }
}

extension Dictionary where Key == String, Value == JSONValue {
    func string(_ key: String, default fallback: String = "") -> String {
        self[key]?.stringValue ?? fallback
    }

    func bool(_ key: String, default fallback: Bool = false) -> Bool {
        self[key]?.boolValue ?? fallback
    }

    func double(_ key: String, default fallback: Double = 0) -> Double {
        self[key]?.doubleValue ?? fallback
    }

    func stringArray(_ key: String) -> [String] {
        self[key]?.arrayValue?.compactMap(\.stringValue) ?? []
    }
}

/// Bridges SwiftUI bindings against a dynamic `[String: JSONValue]` config
/// section (docs/config-schema-rewrite-plan.md §5 phase 4) to plain
/// Swift types, so existing hand-tuned views keep their exact bindings
/// and behavior — only the underlying section type changes from a typed
/// struct to a dynamic dict, closing the hand-mirrored-shape drift the
/// plan targets without forcing every field through the generic
/// SchemaFormView renderer.
extension Binding where Value == [String: JSONValue] {
    func string(_ key: String, default fallback: String = "") -> Binding<String> {
        Binding<String>(
            get: { wrappedValue.string(key, default: fallback) },
            set: { wrappedValue[key] = .string($0) }
        )
    }

    func bool(_ key: String, default fallback: Bool = false) -> Binding<Bool> {
        Binding<Bool>(
            get: { wrappedValue.bool(key, default: fallback) },
            set: { wrappedValue[key] = .bool($0) }
        )
    }

    func double(_ key: String, default fallback: Double = 0) -> Binding<Double> {
        Binding<Double>(
            get: { wrappedValue.double(key, default: fallback) },
            set: { wrappedValue[key] = .number($0) }
        )
    }

    func stringArray(_ key: String) -> Binding<[String]> {
        Binding<[String]>(
            get: { wrappedValue.stringArray(key) },
            set: { wrappedValue[key] = .strings($0) }
        )
    }

    /// dict[str, list[str]]-shaped field nested at `key` (e.g.
    /// discovery.provider_urls[provider]) — the same fixed-known-keys
    /// pattern as dashboard.js's schemaDictListStringsRow.
    func stringArray(_ key: String, subKey: String) -> Binding<[String]> {
        Binding<[String]>(
            get: { wrappedValue[key]?.objectValue?[subKey]?.arrayValue?.compactMap(\.stringValue) ?? [] },
            set: { newValue in
                var object = wrappedValue[key]?.objectValue ?? [:]
                if newValue.isEmpty {
                    object.removeValue(forKey: subKey)
                } else {
                    object[subKey] = .strings(newValue)
                }
                wrappedValue[key] = .object(object)
            }
        )
    }
}
