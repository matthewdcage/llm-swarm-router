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
