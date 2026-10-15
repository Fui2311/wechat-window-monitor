import Cocoa
import ApplicationServices
import Security
import CryptoKit
import Darwin

// Native macOS Accessibility helper. Reads only an independently bound group.
// No OCR, screen scraping, chat submission, file download or Windows selectors.
let arguments = Array(CommandLine.arguments.dropFirst())
let action = arguments.first ?? "authorize"
func option(_ name: String, _ fallback: String = "") -> String {
    guard let index = arguments.firstIndex(of: name), index + 1 < arguments.count else { return fallback }
    return arguments[index + 1]
}
let group = option("--group")
let bundleID = option("--bundle-id", "com.tencent.xinWeChat")
let headerID = option("--header-id")
let listID = option("--list-id")
let resync = max(2, min(60, Double(option("--resync-seconds", "15")) ?? 15))
let followTail = arguments.contains("--follow-tail")
let originalParent = getppid()
var changed = true
var observer: AXObserver?
var observedPID: pid_t = 0
var observedList: CFHashCode = 0

struct Failure: Error { let code: String }
func fail(_ code: String) throws -> Never { throw Failure(code: code) }
func emit(_ value: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]) else { return }
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data([10]))
}
func attribute(_ element: AXUIElement, _ name: String) -> Any? {
    var value: CFTypeRef?
    guard AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success else { return nil }
    return value
}
func text(_ element: AXUIElement, _ name: String) -> String {
    return attribute(element, name) as? String ?? ""
}
func children(_ element: AXUIElement) -> [AXUIElement] {
    return attribute(element, kAXChildrenAttribute) as? [AXUIElement] ?? []
}
func walk(_ root: AXUIElement, limit: Int = 3000) -> [AXUIElement] {
    var result: [AXUIElement] = []
    var pending: [AXUIElement] = [root]
    while !pending.isEmpty && result.count < limit {
        let next = pending.removeLast()
        result.append(next)
        pending.append(contentsOf: children(next).reversed())
    }
    return result
}
func body(_ element: AXUIElement) -> String {
    let direct = text(element, kAXValueAttribute)
    if !direct.isEmpty { return direct.trimmingCharacters(in: .whitespacesAndNewlines) }
    let title = text(element, kAXTitleAttribute)
    if !title.isEmpty { return title.trimmingCharacters(in: .whitespacesAndNewlines) }
    let pieces = walk(element, limit: 200).filter { text($0, kAXRoleAttribute) == kAXStaticTextRole }
        .map { value -> String in
            let string = text(value, kAXValueAttribute)
            return string.isEmpty ? text(value, kAXTitleAttribute) : string
        }.filter { !$0.isEmpty }
    return pieces.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
}
func rectangle(_ element: AXUIElement) -> CGRect? {
    guard let position = attribute(element, kAXPositionAttribute), let size = attribute(element, kAXSizeAttribute),
          CFGetTypeID(position as CFTypeRef) == AXValueGetTypeID(), CFGetTypeID(size as CFTypeRef) == AXValueGetTypeID() else { return nil }
    var point = CGPoint.zero
    var dimensions = CGSize.zero
    if !AXValueGetValue(position as! AXValue, .cgPoint, &point) || !AXValueGetValue(size as! AXValue, .cgSize, &dimensions) { return nil }
    return CGRect(origin: point, size: dimensions)
}
func rows(_ list: AXUIElement) throws -> [[String: Any]] {
    let elements = children(list)
    if elements.count > 200 { try fail("too_many_items") }
    let viewport = rectangle(list)
    var result: [[String: Any]] = []
    let allowed = Set([kAXRowRole, kAXGroupRole, kAXStaticTextRole, kAXTextAreaRole])
    for (index, element) in elements.enumerated() {
        let role = text(element, kAXRoleAttribute)
        if role == kAXScrollBarRole || role == kAXColumnRole || role == "AXHeader" { continue }
        if !allowed.contains(role) { try fail("unverified_row_role") }
        let raw = body(element)
        if raw.isEmpty { continue }
        // Bare time separators are structural, never a source message date.
        if raw.range(of: "^(?:(?:今天|昨天|星期[一二三四五六日天])\\s*)?\\d{1,2}:\\d{2}$", options: .regularExpression) != nil { continue }
        if raw.utf8.count > 120000 { try fail("message_too_large") }
        let rect = rectangle(element)
        if let area = viewport, let item = rect, !area.intersects(item) { continue }
        result.append(["text": raw, "row_index": index, "fully_visible": viewport != nil && rect != nil && viewport!.contains(rect!)])
    }
    if result.count > 199 { try fail("too_many_items") }
    return result
}

struct BoundGroup {
    let application: NSRunningApplication
    let root: AXUIElement
    let window: AXUIElement
    let list: AXUIElement
    let binding: [String: Any]
}
func messageList(_ window: AXUIElement) throws -> AXUIElement {
    let lists = walk(window).filter { element in
        let role = text(element, kAXRoleAttribute)
        return (role == kAXListRole || role == kAXTableRole) && (listID.isEmpty || text(element, kAXIdentifierAttribute) == listID)
    }
    if lists.count != 1 { try fail("message_list_not_unique") }
    return lists[0]
}
func bindGroup() throws -> BoundGroup {
    if group.isEmpty { try fail("invalid_group") }
    if !AXIsProcessTrusted() { try fail("accessibility_permission_required") }
    let applications = NSRunningApplication.runningApplications(withBundleIdentifier: bundleID)
    if applications.count != 1 { try fail(applications.isEmpty ? "process_not_found" : "ambiguous_process") }
    let application = applications[0]
    let root = AXUIElementCreateApplication(application.processIdentifier)
    let windows = attribute(root, kAXWindowsAttribute) as? [AXUIElement] ?? []
    var candidates: [(AXUIElement, [String: Any])] = []
    for window in windows {
        if (attribute(window, kAXMinimizedAttribute) as? Bool) == true { continue }
        if text(window, kAXTitleAttribute) == group {
            candidates.append((window, ["method": "exact_window_title"]))
        } else if !headerID.isEmpty {
            let headers = walk(window).filter { text($0, kAXIdentifierAttribute) == headerID }
            if headers.count == 1 && body(headers[0]) == group {
                candidates.append((window, ["method": "explicit_header_identifier", "header_identifier": headerID, "header_value": group]))
            }
        }
    }
    if candidates.count != 1 { try fail(candidates.isEmpty ? "target_window_not_ready" : "ambiguous_window") }
    let window = candidates[0].0
    return BoundGroup(application: application, root: root, window: window, list: try messageList(window), binding: candidates[0].1)
}
func snapshot(_ bound: BoundGroup) throws -> [String: Any] {
    return ["ok": true, "status": "ready", "source": "macos_ax", "group": group, "bundle_id": bundleID,
            "process_id": Int(bound.application.processIdentifier), "window_title": text(bound.window, kAXTitleAttribute),
            "group_binding": bound.binding, "list_count": 1, "list_role": text(bound.list, kAXRoleAttribute),
            "observed_at": ISO8601DateFormatter().string(from: Date()), "items": try rows(bound.list),
            "diagnostics": ["scope": "loaded target-group Accessibility rows only", "at_tail": NSNull(),
                            "tail_follow_requested": followTail, "history_complete": false]]
}
func subscribe(_ bound: BoundGroup) -> Bool {
    let hash = CFHash(bound.list)
    if observedPID == bound.application.processIdentifier && observedList == hash { return observer != nil }
    if let old = observer { CFRunLoopRemoveSource(CFRunLoopGetCurrent(), AXObserverGetRunLoopSource(old), .defaultMode) }
    observer = nil
    var created: AXObserver?
    let result = AXObserverCreate(bound.application.processIdentifier, { _, _, _, _ in changed = true }, &created)
    guard result == .success, let created = created else { return false }
    observer = created
    observedPID = bound.application.processIdentifier
    observedList = hash
    CFRunLoopAddSource(CFRunLoopGetCurrent(), AXObserverGetRunLoopSource(created), .defaultMode)
    let notifications = ["AXCreated", kAXValueChangedNotification, kAXUIElementDestroyedNotification,
                         kAXSelectedChildrenChangedNotification, "AXChildrenChanged"]
    var boundCount = 0
    for notification in notifications {
        if AXObserverAddNotification(created, bound.list, notification as CFString, nil) == .success { boundCount += 1 }
    }
    return boundCount > 0
}
func idleSeconds() -> Double {
    return [CGEventType.mouseMoved, .leftMouseDown, .rightMouseDown, .keyDown].map {
        CGEventSource.secondsSinceLastEventType(.combinedSessionState, eventType: $0)
    }.min() ?? 0
}

func expand() throws -> [String: Any] {
    let requestData = FileHandle.standardInput.readDataToEndOfFile()
    guard requestData.count < 4096, let request = try JSONSerialization.jsonObject(with: requestData) as? [String: Any],
          let index = request["row_index"] as? Int, let expected = request["text_sha256"] as? String,
          let pid = request["process_id"] as? Int, let title = request["detail_title"] as? String else { try fail("invalid_expansion_request") }
    if title != "群聊的聊天记录" && title.range(of: "^.+和.+的聊天记录$", options: .regularExpression) == nil { try fail("invalid_detail_title") }
    let bound = try bindGroup()
    if Int(bound.application.processIdentifier) != pid || !bound.application.isActive || idleSeconds() < 3 { try fail("foreground_or_input_changed") }
    let elements = children(bound.list)
    if index < 0 || index >= elements.count { try fail("parent_changed") }
    let row = elements[index]
    let raw = body(row)
    let hash = SHA256.hash(data: Data(raw.utf8)).map { String(format: "%02x", $0) }.joined()
    if hash != expected || !raw.hasPrefix("聊天记录") && !raw.hasPrefix("群聊的聊天记录") && !raw.hasPrefix("[聊天记录]") { try fail("parent_changed") }
    let baseline = attribute(bound.root, kAXWindowsAttribute) as? [AXUIElement] ?? []
    if baseline.contains(where: { text($0, kAXTitleAttribute) == title }) { try fail("detail_already_open") }
    let inputBefore = idleSeconds()
    if AXUIElementPerformAction(row, kAXPressAction as CFString) != .success { try fail("card_press_not_supported") }
    let deadline = Date().addingTimeInterval(4)
    var detail: AXUIElement?
    while Date() < deadline {
        if idleSeconds() + 0.2 < inputBefore || !bound.application.isActive { try fail("user_interrupted") }
        let new = (attribute(bound.root, kAXWindowsAttribute) as? [AXUIElement] ?? []).filter { candidate in
            text(candidate, kAXTitleAttribute) == title && !baseline.contains(where: { CFEqual($0, candidate) })
        }
        if new.count > 1 { try fail("ambiguous_detail") }
        if new.count == 1 { detail = new[0]; break }
        Thread.sleep(forTimeInterval: 0.1)
    }
    guard let detail = detail else { try fail("detail_open_timeout") }
    let list = try messageList(detail)
    let listRole = text(list, kAXRoleAttribute)
    let items = try rows(list)
    if idleSeconds() + 0.2 < inputBefore || !bound.application.isActive { try fail("user_interrupted") }
    guard let close = attribute(detail, kAXCloseButtonAttribute), CFGetTypeID(close as CFTypeRef) == AXUIElementGetTypeID() else { try fail("close_not_supported") }
    if AXUIElementPerformAction(close as! AXUIElement, kAXPressAction as CFString) != .success { try fail("detail_close_failed") }
    Thread.sleep(forTimeInterval: 0.15)
    let after = attribute(bound.root, kAXWindowsAttribute) as? [AXUIElement] ?? []
    if after.contains(where: { CFEqual($0, detail) }) { try fail("return_not_verified") }
    _ = try bindGroup()
    return ["ok": true, "status": "ready", "group": group, "source": "macos_ax", "bundle_id": bundleID,
            "process_id": pid, "window_title": title, "list_count": 1, "list_role": listRole,
            "observed_at": ISO8601DateFormatter().string(from: Date()), "items": items,
            "binding": ["process_id": pid, "parent_text_sha256": expected, "parent_row_index": index,
                        "new_window_observed": true, "restored": true]]
}

func credential(_ mode: String) throws {
    let data = FileHandle.standardInput.readDataToEndOfFile()
    guard data.count < 16384, let request = try JSONSerialization.jsonObject(with: data) as? [String: String],
          let service = request["service"], let account = request["account"] else { try fail("invalid_credential_request") }
    let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
                               kSecAttrAccount as String: account]
    if mode == "credential-set" {
        guard let value = request["value"] else { try fail("missing_credential_data") }
        var update = query
        update[kSecValueData as String] = Data(value.utf8)
        let existing = SecItemUpdate(query as CFDictionary, [kSecValueData as String: Data(value.utf8)] as CFDictionary)
        if existing == errSecItemNotFound {
            if SecItemAdd(update as CFDictionary, nil) != errSecSuccess { try fail("keychain_write_failed") }
        } else if existing != errSecSuccess { try fail("keychain_write_failed") }
        emit(["ok": true])
    } else {
        var lookup = query
        lookup[kSecReturnData as String] = true
        lookup[kSecMatchLimit as String] = kSecMatchLimitOne
        var output: CFTypeRef?
        if SecItemCopyMatching(lookup as CFDictionary, &output) != errSecSuccess { try fail("keychain_read_failed") }
        guard let value = output as? Data, let decoded = try JSONSerialization.jsonObject(with: value) as? [String: Any] else { try fail("invalid_keychain_data") }
        emit(decoded)
    }
}

do {
    if action == "self-test" {
        #if arch(arm64)
        let architecture = "arm64"
        #else
        let architecture = "x86_64"
        #endif
        emit(["ok": true, "version": "0.4.0", "architecture": architecture,
              "os": ProcessInfo.processInfo.operatingSystemVersionString])
    } else if action == "authorize" {
        let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
        emit(["ok": AXIsProcessTrustedWithOptions(options), "status": "check_system_settings_accessibility"])
    } else if action.hasPrefix("credential-") {
        try credential(action)
    } else if action == "expand" {
        emit(try expand())
    } else if action == "stream" {
        var last = ""
        var lastRead = Date.distantPast
        while getppid() == originalParent && originalParent > 1 {
            if (changed && Date().timeIntervalSince(lastRead) >= 0.3) || Date().timeIntervalSince(lastRead) >= resync {
                changed = false
                lastRead = Date()
                do {
                    let bound = try bindGroup()
                    if followTail, idleSeconds() >= 3, let lastRow = children(bound.list).last {
                        _ = AXUIElementPerformAction(lastRow, "AXScrollToVisible" as CFString)
                    }
                    let eventBound = subscribe(bound)
                    let result = try snapshot(bound)
                    let stable: [String: Any] = ["items": result["items"]!, "process_id": result["process_id"]!, "window_title": result["window_title"]!]
                    let data = try JSONSerialization.data(withJSONObject: stable, options: [.sortedKeys])
                    let signature = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
                    if signature != last {
                        emit(["kind": "snapshot", "result": result, "event_bound": eventBound])
                        last = signature
                    } else {
                        emit(["kind": "heartbeat", "group": group, "observed_at": result["observed_at"]!,
                              "diagnostics": result["diagnostics"]!, "event_bound": eventBound])
                    }
                } catch {
                    last = ""
                    emit(["kind": "state", "result": ["ok": false, "group": group,
                          "status": (error as? Failure)?.code ?? "macos_capture_failed"]])
                }
            }
            CFRunLoopRunInMode(.defaultMode, min(resync, 1), true)
        }
    } else if action == "probe" || action == "capture" {
        var result = try snapshot(bindGroup())
        if action == "probe" {
            result["message_count"] = (result["items"] as? [[String: Any]])?.count ?? 0
            result.removeValue(forKey: "items")
        }
        emit(result)
    } else { try fail("unsupported_action") }
} catch {
    emit(["ok": false, "group": group, "status": (error as? Failure)?.code ?? "macos_helper_failed"])
    exit(2)
}
