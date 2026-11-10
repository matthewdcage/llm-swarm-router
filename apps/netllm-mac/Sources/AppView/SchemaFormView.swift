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
/// (docs/config-schema-rewrite-plan.md §5 phase 4). Covers every widget
/// the schema emits: toggle/select/number/text/secret/list_strings plus the
/// structured ones (dict_strings, dict_list_strings, dict, list, object),
/// which recurse through `item_schema`. Some call sites still hand-tune
/// their top-level collections (discoveryTab's provider_urls, routingTab's
/// model_pools) for suggestions and layout; the generic widgets are what
/// keep a nested structured field (a source's model_rewrites / scenarios /
/// match) editable instead of being overwritten by a string.
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
                    "0",
                    value: Binding(
                        get: { currentValue.doubleValue ?? 0 },
                        set: { setValue(.number($0)) }
                    ),
                    format: .number.grouping(.never)
                )
                .frame(minWidth: 60, maxWidth: 100)
                .multilineTextAlignment(.trailing)
            }
        case "secret":
            // Write-only, same convention as dashboard.js's
            // schemaSecretRow: config_summary always returns "" for this
            // field (see admin._source_export), and the server-side
            // merge only overwrites a stored secret when the incoming
            // value is non-empty (falsy "" preserves it) — so leaving
            // this blank and saving is always safe.
            HStack {
                Text(label)
                SecureField(
                    override?.placeholder ?? "Leave blank to keep existing",
                    text: Binding(
                        get: { currentValue.stringValue ?? "" },
                        set: { setValue(.string($0)) }
                    )
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
        case "dict_strings":
            SchemaStringDictField(label: label, value: valueBinding)
        case "dict_list_strings":
            SchemaStringListDictField(label: label, value: valueBinding)
        case "object":
            SchemaObjectField(
                label: label,
                itemSchema: field.itemSchema ?? [],
                value: valueBinding
            )
        case "dict":
            SchemaObjectDictField(
                label: label,
                itemSchema: field.itemSchema ?? [],
                value: valueBinding
            )
        case "list":
            SchemaObjectListField(
                label: label,
                itemSchema: field.itemSchema ?? [],
                value: valueBinding
            )
        case "text":
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
        default:
            // A widget this app has no control for. Never fall back to a
            // text field: its binding writes a string, which would replace
            // a structured value the moment the user typed in it.
            HStack {
                Text(label)
                Spacer()
                Text("Edit in the dashboard")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private var valueBinding: Binding<JSONValue> {
        Binding(
            get: { currentValue },
            set: { setValue($0) }
        )
    }
}

// MARK: - Structured widgets

/// "Add" row for a keyed collection: a free-typed key, refused when blank
/// or already present.
private struct SchemaAddKeyRow: View {
    let placeholder: String
    let existing: Set<String>
    let onAdd: (String) -> Void

    @State private var newKey = ""

    private var trimmed: String {
        newKey.trimmingCharacters(in: .whitespaces)
    }

    var body: some View {
        HStack {
            TextField(placeholder, text: $newKey)
            Button("Add") {
                onAdd(trimmed)
                newKey = ""
            }
            .disabled(trimmed.isEmpty || existing.contains(trimmed))
        }
    }
}

private struct SchemaRemoveButton: View {
    let action: () -> Void

    var body: some View {
        Button(role: .destructive, action: action) {
            Image(systemName: "minus.circle")
        }
        .buttonStyle(.borderless)
    }
}

/// dict[str, str] with user-typed keys (a source's model_rewrites).
private struct SchemaStringDictField: View {
    let label: String
    @Binding var value: JSONValue

    private var entries: [String: JSONValue] { value.objectValue ?? [:] }
    private var keys: [String] { entries.keys.sorted() }

    private func update(_ key: String, _ newValue: JSONValue?) {
        var next = entries
        next[key] = newValue
        value = .object(next)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label).font(.caption.weight(.medium))
            ForEach(keys, id: \.self) { key in
                HStack {
                    Text(key).font(.caption.monospaced())
                    Image(systemName: "arrow.right")
                        .foregroundStyle(.secondary)
                    TextField(
                        "",
                        text: Binding(
                            get: { entries[key]?.stringValue ?? "" },
                            set: { update(key, .string($0)) }
                        )
                    )
                    SchemaRemoveButton { update(key, nil) }
                }
            }
            SchemaAddKeyRow(placeholder: "New key", existing: Set(keys)) { key in
                update(key, .string(""))
            }
        }
    }
}

/// dict[str, list[str]] with user-typed keys (routing.model_aliases).
private struct SchemaStringListDictField: View {
    let label: String
    @Binding var value: JSONValue

    private var entries: [String: JSONValue] { value.objectValue ?? [:] }
    private var keys: [String] { entries.keys.sorted() }

    private func update(_ key: String, _ newValue: JSONValue?) {
        var next = entries
        next[key] = newValue
        value = .object(next)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label).font(.caption.weight(.medium))
            ForEach(keys, id: \.self) { key in
                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        Text(key).font(.caption.monospaced())
                        Spacer()
                        SchemaRemoveButton { update(key, nil) }
                    }
                    EditableStringList(
                        items: Binding(
                            get: {
                                entries[key]?.arrayValue?.compactMap(\.stringValue) ?? []
                            },
                            set: { update(key, .strings($0)) }
                        ),
                        placeholder: "",
                        defaultNew: ""
                    )
                }
            }
            SchemaAddKeyRow(placeholder: "New key", existing: Set(keys)) { key in
                update(key, .strings([]))
            }
        }
    }
}

/// One nested model rendered through its own `item_schema` (a source's
/// `match`).
private struct SchemaObjectField: View {
    let label: String
    let itemSchema: [ConfigSchemaField]
    @Binding var value: JSONValue

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label).font(.caption.weight(.medium))
            SchemaFormView(
                fields: itemSchema,
                draft: Binding(
                    get: { value.objectValue ?? [:] },
                    set: { value = .object($0) }
                )
            )
            .padding(.leading, 8)
        }
    }
}

/// dict[str, model] with user-typed keys (a source's scenarios).
private struct SchemaObjectDictField: View {
    let label: String
    let itemSchema: [ConfigSchemaField]
    @Binding var value: JSONValue

    private var entries: [String: JSONValue] { value.objectValue ?? [:] }
    private var keys: [String] { entries.keys.sorted() }

    private func update(_ key: String, _ newValue: JSONValue?) {
        var next = entries
        next[key] = newValue
        value = .object(next)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label).font(.caption.weight(.medium))
            ForEach(keys, id: \.self) { key in
                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        Text(key).font(.caption.monospaced())
                        Spacer()
                        SchemaRemoveButton { update(key, nil) }
                    }
                    SchemaFormView(
                        fields: itemSchema,
                        draft: Binding(
                            get: { entries[key]?.objectValue ?? [:] },
                            set: { update(key, .object($0)) }
                        )
                    )
                    .padding(.leading, 8)
                }
            }
            SchemaAddKeyRow(placeholder: "New name", existing: Set(keys)) { key in
                update(key, .object([:]))
            }
        }
    }
}

/// list[model] rendered row by row through `item_schema`. Bindings are
/// bounds-checked: a row removed while a text field still holds focus must
/// not index past the end.
private struct SchemaObjectListField: View {
    let label: String
    let itemSchema: [ConfigSchemaField]
    @Binding var value: JSONValue

    private var rows: [JSONValue] { value.arrayValue ?? [] }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label).font(.caption.weight(.medium))
            ForEach(Array(rows.indices), id: \.self) { index in
                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        Text("#\(index + 1)").font(.caption.monospaced())
                        Spacer()
                        SchemaRemoveButton {
                            var next = rows
                            guard next.indices.contains(index) else { return }
                            next.remove(at: index)
                            value = .array(next)
                        }
                    }
                    SchemaFormView(
                        fields: itemSchema,
                        draft: Binding(
                            get: {
                                rows.indices.contains(index)
                                    ? rows[index].objectValue ?? [:]
                                    : [:]
                            },
                            set: { newValue in
                                var next = rows
                                guard next.indices.contains(index) else { return }
                                next[index] = .object(newValue)
                                value = .array(next)
                            }
                        )
                    )
                    .padding(.leading, 8)
                }
            }
            Button("Add") {
                value = .array(rows + [.object([:])])
            }
            .buttonStyle(.borderless)
        }
    }
}
