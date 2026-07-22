import SwiftUI

/// Per-field escape hatch for the generic renderer — the Swift twin of
/// dashboard.js's `overrides` map (docs/config-schema-rewrite-plan.md §6
/// risk 1). Covers what the schema can't express: a friendlier label than
/// the field name, a placeholder, or a side effect on change (e.g. the
/// `ui` tab's check_for_updates_automatically starting/stopping a poll
/// timer).
/// One pickable candidate for a list_strings row: the canonical value
/// inserted into config, plus an optional friendlier display label
/// (e.g. value "a1b2c3" labeled "studio-mac (a1b2c3)").
struct SchemaSuggestion: Identifiable, Hashable {
    var value: String
    var label: String

    var id: String { value }

    init(_ value: String, label: String? = nil) {
        self.value = value
        self.label = label ?? value
    }
}

struct SchemaFieldOverride {
    var label: String?
    var placeholder: String?
    /// Known-good candidates for list_strings rows (docs/models-ux-plan.md
    /// phase A): assist, not restrict — free typing stays allowed for
    /// offline/not-yet-seen hosts; unknown values get a soft warning only.
    var suggestions: [SchemaSuggestion]?
    var onChange: ((JSONValue) -> Void)?

    init(
        label: String? = nil,
        placeholder: String? = nil,
        suggestions: [SchemaSuggestion]? = nil,
        onChange: ((JSONValue) -> Void)? = nil
    ) {
        self.label = label
        self.placeholder = placeholder
        self.suggestions = suggestions
        self.onChange = onChange
    }
}

/// Renders every editable (non-read-only) field of one config schema
/// section against a `[String: JSONValue]` draft — the Swift twin of
/// dashboard.js's `renderSchemaForm`/`schemaFieldsCard`
/// (docs/config-schema-rewrite-plan.md §5 phase 4). Covers
/// toggle/select/number/text/list_strings; dict_list_strings and
/// list/dict-of-object widgets stay hand-tuned per call site (see
/// discoveryTab's provider_urls and routingTab's model_pools editor in
/// SettingsWindowView) rather than fully generic, matching how far this
/// phase's risk-scoped migration goes — see the plan doc for why.
struct SchemaFormView: View {
    let fields: [ConfigSchemaField]
    @Binding var draft: [String: JSONValue]
    var overrides: [String: SchemaFieldOverride] = [:]

    var body: some View {
        ForEach(fields.filter { !($0.readOnly ?? false) }) { field in
            SchemaFieldRow(field: field, draft: $draft, override: overrides[field.name])
        }
    }
}

private struct SchemaFieldRow: View {
    let field: ConfigSchemaField
    @Binding var draft: [String: JSONValue]
    var override: SchemaFieldOverride?

    private var label: String {
        if let override = override?.label { return override }
        return field.name
            .replacingOccurrences(of: "_", with: " ")
            .capitalized
    }

    private var currentValue: JSONValue {
        draft[field.name] ?? field.fieldDefault ?? .null
    }

    private func setValue(_ value: JSONValue) {
        draft[field.name] = value
        override?.onChange?(value)
    }

    var body: some View {
        switch field.widget {
        case "toggle":
            Toggle(label, isOn: Binding(
                get: { currentValue.boolValue ?? false },
                set: { setValue(.bool($0)) }
            ))
        case "select":
            Picker(label, selection: Binding(
                get: { currentValue.stringValue ?? "" },
                set: { setValue(.string($0)) }
            )) {
                ForEach(field.options ?? [], id: \.self) { option in
                    Text(option.isEmpty ? "(default)" : option).tag(option)
                }
            }
        case "number":
            HStack {
                Text(label)
                TextField(
                    "",
                    value: Binding(
                        get: { currentValue.doubleValue ?? 0 },
                        set: { setValue(.number($0)) }
                    ),
                    format: .number
                )
            }
        case "list_strings":
            VStack(alignment: .leading, spacing: 4) {
                Text(label).font(.caption.weight(.medium))
                EditableStringList(
                    items: Binding(
                        get: { currentValue.arrayValue?.compactMap(\.stringValue) ?? [] },
                        set: { setValue(.strings($0)) }
                    ),
                    placeholder: override?.placeholder ?? "",
                    defaultNew: override?.placeholder ?? "",
                    suggestions: override?.suggestions ?? []
                )
            }
        default:
            // "text" and any widget not yet given a dedicated Swift
            // control (secret/list_strings/dict_list_strings/list/dict —
            // see dashboard.js's equivalents) render as a plain text
            // field rather than silently omitting the field.
            HStack {
                Text(label)
                TextField(
                    override?.placeholder ?? "",
                    text: Binding(
                        get: { currentValue.stringValue ?? "" },
                        set: { setValue(.string($0)) }
                    )
                )
            }
        }
    }
}
