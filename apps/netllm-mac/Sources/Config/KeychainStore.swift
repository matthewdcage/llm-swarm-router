import Foundation
import Security

enum KeychainStore {
    private static let service = "netllm"

    enum Account {
        static let anthropicAPIKey = "anthropic_api_key"
        static let openaiAPIKey = "openai_api_key"
        static let moonshotAPIKey = "moonshot_api_key"
        static let zaiAPIKey = "zai_api_key"
        static let openrouterAPIKey = "openrouter_api_key"
    }

    /// Maps a cloud provider registry id (from netllm_core.cloud_providers,
    /// served at GET /netllm/v1/cloud/providers) to its Keychain account.
    /// The single place this mapping lives — AgentAPI.cloudProviderRegistry
    /// and SettingsViewModel.cloudProviders (offline bootstrap) both use it
    /// instead of hand-rolling their own id -> account switch.
    static func accountForCloudProvider(_ providerId: String) -> String {
        switch providerId {
        case "anthropic": return Account.anthropicAPIKey
        case "openai": return Account.openaiAPIKey
        case "moonshot": return Account.moonshotAPIKey
        case "zai": return Account.zaiAPIKey
        case "openrouter": return Account.openrouterAPIKey
        default: return "\(providerId)_api_key"
        }
    }

    /// Which environment variable each stored key has to be exported as when
    /// the app launches the agent.
    ///
    /// This used to be a closed `[(account, envVar)]` table in
    /// `PythonRuntime.injectCloudAPIKeys`, and it was the one hardcode in the
    /// tree that failed silently: a provider added to
    /// `netllm_core.cloud_providers` was rendered, saved to the Keychain and
    /// never exported, so it 401'd while the Settings window showed the key
    /// as stored. The list now comes from the registry the agent already
    /// serves at `GET /netllm/v1/cloud/providers` (`api_key_env` per row),
    /// remembered across launches because `makeEnvironment()` runs *before*
    /// there is an agent to ask.
    enum CloudKeyEnv {
        private static let defaultsKey = "netllm.cloudProviderAPIKeyEnv"

        /// Matches every `CloudProviderSpec.api_key_env` in the Python
        /// registry, asserted by `test_cloud_provider_api_key_env_is_derivable`.
        static func defaultEnvVar(for providerId: String) -> String {
            "\(providerId.uppercased())_API_KEY"
        }

        /// Providers the Settings window can render — and therefore store a
        /// key for — before it has ever reached a running agent. Anything
        /// beyond this arrives through the registry, so remembered ∪ bootstrap
        /// is a complete cover of what the Keychain can be holding.
        static let bootstrapProviderIDs = [
            "moonshot", "zai", "openai", "anthropic", "openrouter",
        ]

        static func remember(_ providers: [CloudProviderInfo]) {
            var map: [String: String] = [:]
            for provider in providers {
                map[provider.id] = provider.resolvedAPIKeyEnv
            }
            guard !map.isEmpty else { return }
            UserDefaults.standard.set(map, forKey: defaultsKey)
        }

        static func remembered() -> [String: String] {
            UserDefaults.standard.dictionary(forKey: defaultsKey) as? [String: String]
                ?? [:]
        }

        /// Every (Keychain account, env var) pair worth exporting. Pure, so
        /// the tests do not need a Keychain or a UserDefaults suite.
        static func injectionPairs(
            remembered: [String: String]
        ) -> [(account: String, envVar: String)] {
            var envById = remembered
            for id in bootstrapProviderIDs where envById[id] == nil {
                envById[id] = defaultEnvVar(for: id)
            }
            return envById.keys.sorted().map { id in
                (
                    account: KeychainStore.accountForCloudProvider(id),
                    envVar: envById[id] ?? defaultEnvVar(for: id)
                )
            }
        }
    }

    static func load(account: String) -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        guard status == errSecSuccess, let data = item as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    static func save(account: String, value: String) throws {
        let encoded = Data(value.utf8)
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        let attributes: [String: Any] = [
            kSecValueData as String: encoded,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlock,
        ]
        let updateStatus = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        if updateStatus == errSecSuccess {
            return
        }
        if updateStatus == errSecItemNotFound {
            var addQuery = query
            addQuery[kSecValueData as String] = encoded
            addQuery[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
            let addStatus = SecItemAdd(addQuery as CFDictionary, nil)
            guard addStatus == errSecSuccess else {
                throw KeychainError.saveFailed(addStatus)
            }
            return
        }
        throw KeychainError.saveFailed(updateStatus)
    }

    static func delete(account: String) {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        SecItemDelete(query as CFDictionary)
    }

    enum KeychainError: Error {
        case saveFailed(OSStatus)
    }
}
