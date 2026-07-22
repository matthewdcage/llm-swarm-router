import Foundation

/// Mirrors netllm_core.config_schema.config_schema_document()'s shape —
/// see docs/config-schema-rewrite-plan.md. Fetched via `netllm config
/// schema` (ConfigStore.loadSchema), the same document the dashboard
/// fetches over HTTP from GET /netllm/v1/config/schema.
struct ConfigSchema: Codable, Sendable {
    var version: String
    var sections: [String: ConfigSchemaSection]
}

struct ConfigSchemaSection: Codable, Sendable {
    var fields: [ConfigSchemaField]
}

struct ConfigSchemaField: Codable, Sendable, Identifiable, Equatable {
    var id: String { name }
    var name: String
    var type: String
    var widget: String
    var optional: Bool?
    var options: [String]?
    var writeOnly: Bool?
    var readOnly: Bool?
    var group: String?
    var optionsFrom: String?
    var defaultFactory: String?
    var help: String?
    var itemSchema: [ConfigSchemaField]?
    var fieldDefault: JSONValue?

    enum CodingKeys: String, CodingKey {
        case name, type, widget, optional, options, help
        case writeOnly = "write_only"
        case readOnly = "read_only"
        case group
        case optionsFrom = "options_from"
        case defaultFactory = "default_factory"
        case itemSchema = "item_schema"
        case fieldDefault = "default"
    }

    static func == (lhs: ConfigSchemaField, rhs: ConfigSchemaField) -> Bool {
        lhs.name == rhs.name && lhs.widget == rhs.widget
    }
}
