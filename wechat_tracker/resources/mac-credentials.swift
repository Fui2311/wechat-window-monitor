import Foundation
import Security

// This deliberately separate executable is not rebuilt with capture/UI fixes.
// Keychain access remains scoped to the existing application credential item.
func emit(_ value: [String: Any]) {
    let data = try! JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data([10]))
}
func fail(_ status: String) -> Never {
    emit(["ok": false, "status": status])
    exit(2)
}
let action = CommandLine.arguments.dropFirst().first ?? "self-test"
if action == "self-test" {
    emit(["ok": true, "version": 1, "storage": "macOS Keychain", "credential_access_performed": false])
    exit(0)
}
guard ["credential-get", "credential-set", "credential-authorize"].contains(action) else { fail("unsupported_action") }
// Routine background reads must never open password panels. Only an explicit
// authorization/setup action may ask the user to grant Keychain access.
SecKeychainSetUserInteractionAllowed(action != "credential-get")
let input = FileHandle.standardInput.readDataToEndOfFile()
guard input.count < 32768, let request = (try? JSONSerialization.jsonObject(with: input)) as? [String: String],
      request["service"] == "wechat-lecture-tracker", let account = request["account"],
      !account.isEmpty, account.utf8.count < 1024 else { fail("invalid_credential_request") }
let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                          kSecAttrService as String: "wechat-lecture-tracker", kSecAttrAccount as String: account]
if action == "credential-set" {
    guard let value = request["value"], (try? JSONSerialization.jsonObject(with: Data(value.utf8))) is [String: String] else {
        fail("invalid_credential_data")
    }
    let data = Data(value.utf8)
    let status = SecItemUpdate(query as CFDictionary, [kSecValueData as String: data] as CFDictionary)
    if status == errSecItemNotFound {
        var item = query
        item[kSecValueData as String] = data
        guard SecItemAdd(item as CFDictionary, nil) == errSecSuccess else { fail("keychain_write_failed") }
    } else if status != errSecSuccess { fail("keychain_write_failed") }
    emit(["ok": true])
} else {
    var lookup = query
    lookup[kSecReturnData as String] = true
    lookup[kSecMatchLimit as String] = kSecMatchLimitOne
    var output: CFTypeRef?
    guard SecItemCopyMatching(lookup as CFDictionary, &output) == errSecSuccess else { fail("keychain_read_failed") }
    guard let data = output as? Data, let values = (try? JSONSerialization.jsonObject(with: data)) as? [String: String] else {
        fail("invalid_keychain_data")
    }
    if action == "credential-authorize" { emit(["ok": true]) }
    else { emit(values) }
}
