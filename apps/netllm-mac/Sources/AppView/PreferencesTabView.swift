import SwiftUI

@MainActor
struct PreferencesTabView: View {
    @Bindable var model: SettingsViewModel
    @Bindable var updateController: UpdateController
    var onUiSettingsChanged: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text("Preferences")
                .font(.largeTitle.weight(.semibold))

            SettingsSectionTitle(title: "Appearance")
            SettingsSurfaceCard {
                Toggle("Show CPU gauge", isOn: $model.document.ui.bool("menubar_show_cpu"))
                Toggle("Show GPU gauge", isOn: $model.document.ui.bool("menubar_show_gpu"))
                Toggle("Show memory gauge", isOn: $model.document.ui.bool("menubar_show_mem"))
                Toggle("Show live throughput (LIV)", isOn: $model.document.ui.bool("menubar_show_live"))
                Toggle(
                    "Models menu: favorites only",
                    isOn: $model.document.ui.bool("menubar_models_favorites_only")
                )
            }

            SettingsSectionTitle(title: "Behaviour")
            SettingsSurfaceCard {
                LoginItemSettings()
                if let uiFields = model.configSchema?.sections["ui"]?.fields {
                    SchemaFormView(
                        fields: uiFields.filter {
                            !$0.name.hasPrefix("menubar_")
                        },
                        draft: $model.document.ui,
                        overrides: [
                            "auto_start_on_launch": SchemaFieldOverride(
                                label: "Auto-start agent on launch"
                            ),
                            "check_for_updates_automatically": SchemaFieldOverride(
                                label: "Check for updates automatically",
                                onChange: { value in
                                    if value.boolValue == true {
                                        updateController.restartPollingIfNeeded()
                                    } else {
                                        updateController.stopPolling()
                                    }
                                }
                            ),
                            "log_dir": SchemaFieldOverride(label: "Log directory", placeholder: "default"),
                        ]
                    )
                }
                Button("Reveal log directory in Finder") { revealLogDirectory() }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
            }

            SettingsSectionTitle(title: "Updates")
            UpdateBannerCard(controller: updateController)

            SettingsSectionTitle(title: "About")
            SettingsSurfaceCard {
                SettingsInfoRow(label: "App version", value: AppVersionInfo.display)
                SettingsInfoRow(label: "Platform", value: AppVersionInfo.platformLine)
                SettingsInfoRow(label: "Config file", value: AppConfig.defaultConfigPath().path)
                SettingsInfoRow(label: "CLI", value: AppBranding.cliCommand)
            }
        }
        .onChange(of: model.document.ui) { _, _ in
            onUiSettingsChanged()
        }
    }

    private func revealLogDirectory() {
        let dir = LogPaths.logDirFromConfigFile()
        NSWorkspace.shared.open(dir)
    }
}
