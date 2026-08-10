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
    /// The row's stable opaque identity (`row_id`). Always `readOnly` too —
    /// no surface renders a control for it — but the two flags mean opposite
    /// things to anything that builds a patch: `readOnly` says "drop it",
    /// `identity` says "send it back exactly as received, so the server can
    /// tell which stored row this edit belongs to". Drop it and editing a
    /// backend's base_url or a source's id reads server-side as delete+create,
    /// which erases that row's write-only api_key/secret.
    ///
    /// Nothing in this app filters a patch by `readOnly` today — `routing`
    /// rows are encoded whole from `NetllmConfigDocument` — so this is
    /// modelled rather than consumed. It is here so that a future patch
    /// builder written against this struct has the distinction available
    /// instead of re-deriving the bug.
    var identity: Bool?
    var group: String?
    var optionsFrom: String?
    var defaultFactory: String?
    var help: String?
    var itemSchema: [ConfigSchemaField]?
    var fieldDefault: JSONValue?

    enum CodingKeys: String, CodingKey {
        case name, type, widget, optional, options, help, identity
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
