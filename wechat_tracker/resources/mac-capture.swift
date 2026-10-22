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
let forwardListID = option("--forward-list-id")
let resync = max(2, min(60, Double(option("--resync-seconds", "15")) ?? 15))
let followTail = arguments.contains("--follow-tail")
let recoverWindow = arguments.contains("--recover-window")
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
    var occurrences: [String: Int] = [:]
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
        // Count against every loaded row before clipping to the viewport.
        // Otherwise an identical new card becomes occurrence zero as soon as
        // the older copy scrolls out, colliding with the saved baseline.
        let occurrence = occurrences[raw, default: 0]
        occurrences[raw] = occurrence + 1
        let rect = rectangle(element)
        if let area = viewport, let item = rect, !area.intersects(item) { continue }
        result.append(["text": raw, "row_index": index, "row_token": String(CFHash(element)), "content_ordinal": occurrence,
                       "fully_visible": viewport != nil && rect != nil && viewport!.contains(rect!)])
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
var lastBoundGroup: BoundGroup?
var applicationRecoveryDeferredUntil = Date.distantPast
func ownedSystemWindows(_ pid: pid_t) -> [[String: Any]] {
    return (CGWindowListCopyWindowInfo(.optionAll, kCGNullWindowID) as? [[String: Any]] ?? []).filter {
        ($0[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value == pid
    }
}
func messageList(_ window: AXUIElement, identifier: String? = nil) throws -> AXUIElement {
    let expectedID = identifier ?? listID
    let lists = walk(window).filter { element in
        let role = text(element, kAXRoleAttribute)
        return (role == kAXListRole || role == kAXTableRole) && (expectedID.isEmpty || text(element, kAXIdentifierAttribute) == expectedID)
    }
    if lists.count != 1 { try fail("message_list_not_unique") }
    return lists[0]
}
func bindGroup(recover: Bool = false, allowActivation: Bool = true, recoveryPurpose: String? = nil) throws -> BoundGroup {
    if group.isEmpty { try fail("invalid_group") }
    if !AXIsProcessTrusted() { try fail("accessibility_permission_required") }
    if desktopLocked() { try fail("desktop_locked") }
    let applications = NSRunningApplication.runningApplications(withBundleIdentifier: bundleID)
    if applications.count != 1 { try fail(applications.isEmpty ? "process_not_found" : "ambiguous_process") }
    let application = applications[0]
    let root = AXUIElementCreateApplication(application.processIdentifier)
    var windows = attribute(root, kAXWindowsAttribute) as? [AXUIElement] ?? []
    // Qt briefly withdraws AX windows during Space/application activation.
    // Wait for that transition before classifying the target as unavailable.
    let windowDeadline = Date().addingTimeInterval(0.6)
    while windows.isEmpty && Date() < windowDeadline {
        CFRunLoopRunInMode(.defaultMode, 0.05, true)
        windows = attribute(root, kAXWindowsAttribute) as? [AXUIElement] ?? []
    }
    var candidates: [(AXUIElement, [String: Any])] = []
    for window in windows {
        if text(window, kAXTitleAttribute) == group {
            candidates.append((window, ["method": "exact_window_title"]))
        } else if !headerID.isEmpty {
            let headers = walk(window).filter { text($0, kAXIdentifierAttribute) == headerID }
            if headers.count == 1 && body(headers[0]) == group {
                candidates.append((window, ["method": "explicit_header_identifier", "header_identifier": headerID, "header_value": group]))
            }
        }
    }
    // Keep a previously root-bound AX window usable across background/Space
    // transitions. Recheck its PID, exact title, live list and WindowServer
    // ownership; a stale or closed element cannot satisfy these checks.
    if candidates.isEmpty, let cached = lastBoundGroup,
       cached.application.processIdentifier == application.processIdentifier,
       text(cached.window, kAXTitleAttribute) == group {
        let owned = ownedSystemWindows(application.processIdentifier)
        let titlesRedacted = !owned.isEmpty && owned.allSatisfy { ($0[kCGWindowName as String] as? String ?? "").isEmpty }
        let matches = owned.filter { ($0[kCGWindowName as String] as? String) == group }
        if (matches.count == 1 || titlesRedacted), let list = try? messageList(cached.window) {
            candidates.append((cached.window, ["method": "exact_window_title", "cached_window_revalidated": true]))
            // The list is queried again below when constructing the binding.
            _ = list
        }
    }
    if candidates.isEmpty && recover && allowActivation {
        // Without Screen Recording permission, launchd children receive no
        // WindowServer titles. Activation of this already verified app is
        // allowed, but no group read/click is allowed until exact AX rebinding
        // succeeds below. Never search for or select a conversation.
        let owned = ownedSystemWindows(application.processIdentifier)
        let titlesRedacted = !owned.isEmpty && owned.allSatisfy { ($0[kCGWindowName as String] as? String ?? "").isEmpty }
        let matches = owned.filter { ($0[kCGWindowName as String] as? String) == group }
        if matches.count > 1 { try fail("ambiguous_window") }
        if matches.isEmpty && !titlesRedacted { try fail("target_window_not_ready") }
        if Date() < applicationRecoveryDeferredUntil { try fail("window_recovery_cooldown") }
        if Date() < tailDeferredUntil { try fail("user_postponed") }
        if idleSeconds() < 3 { try fail("window_recovery_user_busy") }
        applicationRecoveryDeferredUntil = Date().addingTimeInterval(300)
        guard let lease = UIActionLease() else { try fail("window_recovery_user_busy") }
        defer { lease.release() }
        guard let previous = NSWorkspace.shared.frontmostApplication, let url = application.bundleURL else {
            try fail("foreground_unknown")
        }
        let noticeSeconds: Int
        do {
            noticeSeconds = try forwardNotice(foreground: previous.processIdentifier, purpose: recoveryPurpose ?? "恢复“\(group)”的现有窗口")
        } catch let error as Failure {
            if ["user_postponed", "user_active_during_notice", "user_interrupted"].contains(error.code) {
                let delay = error.code == "user_postponed" ? (Double(option("--forward-postpone-seconds", "300")) ?? 300) : 3
                tailDeferredUntil = Date().addingTimeInterval(delay)
            }
            throw error
        }
        if controlIdleSeconds() < 3 { try fail("window_recovery_user_busy") }
        let clock = Date(), idle = controlIdleSeconds()
        defer {
            if previous.processIdentifier != application.processIdentifier,
               NSWorkspace.shared.frontmostApplication?.processIdentifier == application.processIdentifier,
               controlIdleSeconds() + 0.25 >= idle + Date().timeIntervalSince(clock), let previousURL = previous.bundleURL {
                let config = NSWorkspace.OpenConfiguration()
                config.activates = true
                config.addsToRecentItems = false
                NSWorkspace.shared.openApplication(at: previousURL, configuration: config) { _, _ in }
                let deadline = Date().addingTimeInterval(3)
                while NSWorkspace.shared.frontmostApplication?.processIdentifier != previous.processIdentifier && Date() < deadline {
                    CFRunLoopRunInMode(.defaultMode, 0.04, true)
                }
            }
        }
        if application.isHidden && !application.unhide() { try fail("window_restore_failed") }
        let config = NSWorkspace.OpenConfiguration()
        config.activates = true
        config.addsToRecentItems = false
        NSWorkspace.shared.openApplication(at: url, configuration: config) { _, _ in }
        let deadline = Date().addingTimeInterval(3)
        repeat {
            CFRunLoopRunInMode(.defaultMode, 0.05, true)
            if controlIdleSeconds() + 0.25 < idle + Date().timeIntervalSince(clock) { try fail("user_interrupted") }
            if NSWorkspace.shared.frontmostApplication?.processIdentifier == application.processIdentifier,
               let rebound = try? bindGroup(recover: true, allowActivation: false) {
                var binding = rebound.binding
                binding["application_window_recovered"] = true
                binding["recovery_notice_seconds"] = noticeSeconds
                return BoundGroup(application: rebound.application, root: rebound.root, window: rebound.window,
                                  list: rebound.list, binding: binding)
            }
        } while Date() < deadline
        try fail("window_restore_failed")
    }
    if candidates.count != 1 { try fail(candidates.isEmpty ? "target_window_not_ready" : "ambiguous_window") }
    let window = candidates[0].0
    var binding = candidates[0].1
    binding["window_restored"] = false
    if (attribute(window, kAXMinimizedAttribute) as? Bool) == true {
        if !recover { try fail("target_window_minimized") }
        if desktopLocked() { try fail("desktop_locked") }
        if idleSeconds() < 3 { try fail("window_recovery_user_busy") }
        // Only the independently bound window may be restored. Never open a
        // conversation by searching or selecting a similarly named contact.
        if AXUIElementSetAttributeValue(window, kAXMinimizedAttribute as CFString, kCFBooleanFalse) != .success {
            try fail("window_restore_failed")
        }
        CFRunLoopRunInMode(.defaultMode, 0.15, true)
        if (attribute(window, kAXMinimizedAttribute) as? Bool) != false { try fail("window_restore_failed") }
        binding["window_restored"] = true
    }
    let bound = BoundGroup(application: application, root: root, window: window, list: try messageList(window), binding: binding)
    lastBoundGroup = bound
    return bound
}
func desktopLocked() -> Bool {
    return (CGSessionCopyCurrentDictionary() as? [String: Any])?["CGSSessionScreenIsLocked"] as? Bool == true
}
func verticalScrollbars(_ list: AXUIElement) -> [AXUIElement] {
    var node = list
    // Qt may expose the scrollbar on the list's enclosing scroll area.
    for _ in 0..<3 {
        let bars = children(node).filter {
            text($0, kAXRoleAttribute) == kAXScrollBarRole &&
            text($0, kAXOrientationAttribute) != kAXHorizontalOrientationValue
        }
        if !bars.isEmpty { return bars }
        guard let parent = attribute(node, kAXParentAttribute), CFGetTypeID(parent as CFTypeRef) == AXUIElementGetTypeID() else { break }
        node = parent as! AXUIElement
        if text(node, kAXRoleAttribute) == kAXWindowRole { break }
    }
    return []
}
func atTail(_ list: AXUIElement) -> Bool? {
    let bars = verticalScrollbars(list)
    guard bars.count == 1, let value = attribute(bars[0], kAXValueAttribute) as? Double,
          let maximum = attribute(bars[0], kAXMaxValueAttribute) as? Double else { return nil }
    return value >= maximum - 0.001
}
var lastTailAttempt = Date.distantPast
var lastRecoveredViewport = ""
var lastTailRecovery = Date.distantPast
var tailDeferredUntil = Date.distantPast
var tailControlDiagnostic: [String: Any] = [:]
func viewportSignature(_ bound: BoundGroup) throws -> String {
    let loaded = children(bound.list)
    let value: [String: Any] = ["visible": try rows(bound.list), "loaded_count": loaded.count,
                              "last_row": loaded.last.map { String(CFHash($0)) + body($0) } ?? ""]
    let data = try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
    return SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
}
func recoverTailActivated(_ bound: BoundGroup) throws -> [String: Any] {
    guard let previous = NSWorkspace.shared.frontmostApplication else { try fail("foreground_unknown") }
    let pid = bound.application.processIdentifier
    if previous.processIdentifier != pid && bound.binding["recovery_notice_seconds"] == nil {
        _ = try forwardNotice(foreground: previous.processIdentifier, purpose: "恢复“\(group)”的消息视口并滚到最新消息")
    }
    if desktopLocked() || controlIdleSeconds() < 3 { try fail("window_recovery_user_busy") }
    var interrupted = false
    func checkControl(_ stage: String) throws {
        let foreground = NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0
        let idle = controlIdleSeconds()
        tailControlDiagnostic = ["stage": stage, "foreground_pid": Int(foreground), "expected_pid": Int(pid), "control_idle_seconds": idle]
        if idle < 3 || foreground != pid {
            interrupted = true
            try fail("user_interrupted")
        }
    }
    func activate(_ app: NSRunningApplication) throws {
        if NSWorkspace.shared.frontmostApplication?.processIdentifier == app.processIdentifier { return }
        guard let url = app.bundleURL else { try fail("activation_url_missing") }
        let configuration = NSWorkspace.OpenConfiguration()
        configuration.activates = true
        configuration.addsToRecentItems = false
        NSWorkspace.shared.openApplication(at: url, configuration: configuration) { _, _ in }
        let deadline = Date().addingTimeInterval(3)
        while NSWorkspace.shared.frontmostApplication?.processIdentifier != app.processIdentifier && Date() < deadline {
            CFRunLoopRunInMode(.defaultMode, 0.04, true)
        }
        if NSWorkspace.shared.frontmostApplication?.processIdentifier != app.processIdentifier { try fail("activation_failed") }
    }
    defer {
        if !interrupted && NSWorkspace.shared.frontmostApplication?.processIdentifier == pid { try? activate(previous) }
    }
    try activate(bound.application)
    if AXUIElementPerformAction(bound.window, kAXRaiseAction as CFString) != .success { try fail("group_window_raise_failed") }
    CFRunLoopRunInMode(.defaultMode, 0.1, true)
    if !CFEqual(try bindGroup().window, bound.window) { try fail("parent_changed") }
    // Fail closed if a dialog, other chat, or another app covers this list.
    // Wait for the activation/Space animation to settle, reacquiring geometry
    // each time; foreground PID alone does not prove the window is on screen.
    var point = CGPoint.zero
    var inside = false
    let hitDeadline = Date().addingTimeInterval(1.2)
    repeat {
        try checkControl("hit_test")
        let current = try bindGroup()
        if !CFEqual(current.window, bound.window) { try fail("parent_changed") }
        guard let area = rectangle(current.list), area.width > 40, area.height > 40 else { try fail("geometry_unavailable") }
        point = CGPoint(x: area.midX, y: area.midY)
        var hit: AXUIElement?
        _ = AXUIElementCopyElementAtPosition(AXUIElementCreateSystemWide(), Float(point.x), Float(point.y), &hit)
        var ancestor = hit
        for _ in 0..<12 {
            guard let element = ancestor else { break }
            if CFEqual(element, current.list) { inside = true; break }
            guard let parent = attribute(element, kAXParentAttribute), CFGetTypeID(parent as CFTypeRef) == AXUIElementGetTypeID() else { break }
            ancestor = (parent as! AXUIElement)
        }
        if !inside { CFRunLoopRunInMode(.defaultMode, 0.04, true) }
    } while !inside && Date() < hitDeadline
    if !inside { try fail("list_occluded") }
    try checkControl("before_wheel")
    let cursor = CGEvent(source: nil)?.location ?? .zero
    guard let event = CGEvent(scrollWheelEvent2Source: CGEventSource(stateID: .privateState),
                              units: .pixel, wheelCount: 1, wheel1: -20000, wheel2: 0, wheel3: 0) else { try fail("event_creation_failed") }
    event.location = point
    event.post(tap: .cghidEventTap)
    // Observe our event only after WindowServer has delivered it; sampling
    // the input clock before delivery mistakes our wheel for user input.
    CFRunLoopRunInMode(.defaultMode, 0.15, true)
    let inputClock = Date()
    let idleAfterOwnInput = controlIdleSeconds()
    CFRunLoopRunInMode(.defaultMode, 0.2, true)
    let after = CGEvent(source: nil)?.location ?? .zero
    // A posted wheel event can update the system's event location. Restore
    // only our own point; a different pointer position belongs to the user.
    if hypot(after.x-point.x, after.y-point.y) < 2 { CGWarpMouseCursorPosition(cursor) }
    let restoredCursor = CGEvent(source: nil)?.location ?? .zero
    let cursorRestored = hypot(restoredCursor.x-cursor.x, restoredCursor.y-cursor.y) <= 2
    tailControlDiagnostic = ["stage": "after_wheel", "foreground_pid": Int(NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0),
                             "expected_pid": Int(pid), "control_idle_seconds": controlIdleSeconds(),
                             "idle_after_own_input": idleAfterOwnInput, "elapsed": Date().timeIntervalSince(inputClock)]
    if NSWorkspace.shared.frontmostApplication?.processIdentifier != pid ||
       controlIdleSeconds() + 0.08 < idleAfterOwnInput + Date().timeIntervalSince(inputClock) {
        interrupted = true
        try fail("user_interrupted")
    }
    try activate(previous)
    if NSWorkspace.shared.frontmostApplication?.processIdentifier != previous.processIdentifier { try fail("foreground_restore_failed") }
    return ["foreground_pid_before": Int(previous.processIdentifier),
            "foreground_pid_after": Int(NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0),
            "foreground_restored": true, "cursor_restored": cursorRestored,
            "cursor_restore_skipped_user_motion": !cursorRestored, "global_input_used": true]
}
func followLatest(_ bound: BoundGroup) throws -> [String: Any] {
    var result: [String: Any] = ["requested": followTail, "action": "none"]
    if !followTail { return result }
    if desktopLocked() { try fail("desktop_locked") }
    if Date() < tailDeferredUntil { result["action"] = "deferred_recovery"; return result }
    if idleSeconds() < 3 { result["action"] = "deferred_user_busy"; return result }
    if atTail(bound.list) == true { result["action"] = "already_at_tail"; return result }
    // An unchanged old viewport cannot prove that no new messages arrived
    // below it. Periodically recheck even when Qt emits no list notification.
    if try viewportSignature(bound) == lastRecoveredViewport && Date().timeIntervalSince(lastTailRecovery) < 300 {
        result["action"] = "unchanged_recovered_viewport"; return result
    }
    // AX notifications caused by scrolling cannot trigger an input feedback loop.
    if Date().timeIntervalSince(lastTailAttempt) < resync { result["action"] = "cooldown"; return result }
    guard let lease = UIActionLease() else { result["action"] = "deferred_ui_busy"; return result }
    defer { lease.release() }
    lastTailAttempt = Date()
    let bars = verticalScrollbars(bound.list)
    if bars.count == 1, let maximum = attribute(bars[0], kAXMaxValueAttribute) {
        if AXUIElementSetAttributeValue(bars[0], kAXValueAttribute as CFString, maximum as CFTypeRef) == .success {
            CFRunLoopRunInMode(.defaultMode, 0.1, true)
            result["action"] = "scrollbar_to_maximum"
            if atTail(bound.list) == true { return result }
        }
    }
    // Reacquire the last loaded row after each successful action: a virtual
    // list can load another page instead of reaching the newest message.
    var previous = ""
    var actions = 0
    for _ in 0..<8 {
        if idleSeconds() < 3 { break }
        guard let row = children(bound.list).last else { break }
        let signature = body(row)
        if signature == previous { break }
        previous = signature
        if AXUIElementPerformAction(row, "AXScrollToVisible" as CFString) != .success { break }
        actions += 1
        CFRunLoopRunInMode(.defaultMode, 0.08, true)
    }
    result["row_actions"] = actions
    result["action"] = actions > 0 ? "last_loaded_row" : "scroll_action_unavailable"
    if recoverWindow && atTail(bound.list) != true && idleSeconds() >= 3 {
        do {
            result.merge(try recoverTailActivated(bound)) { _, new in new }
        } catch let error as Failure {
            if ["user_postponed", "user_active_during_notice", "user_interrupted", "window_recovery_user_busy", "list_occluded"].contains(error.code) {
                let delay = error.code == "user_postponed" ? (Double(option("--forward-postpone-seconds", "300")) ?? 300) : (error.code == "list_occluded" ? 60 : 3)
                tailDeferredUntil = Date().addingTimeInterval(delay)
                // A deferred UI action is not a capture failure. Continue to
                // read the independently bound viewport and retry later.
                result["action"] = "deferred_recovery"
                result["error"] = error.code
                result["control"] = tailControlDiagnostic
                return result
            }
            throw error
        }
        lastRecoveredViewport = try viewportSignature(try bindGroup())
        lastTailRecovery = Date()
        result["action"] = "activated_scroll_to_latest"
    }
    return result
}
func snapshot(_ bound: BoundGroup) throws -> [String: Any] {
    return ["ok": true, "status": "ready", "source": "macos_ax", "group": group, "bundle_id": bundleID,
            "process_id": Int(bound.application.processIdentifier), "window_title": text(bound.window, kAXTitleAttribute),
            "group_binding": bound.binding, "list_count": 1, "list_role": text(bound.list, kAXRoleAttribute),
            "observed_at": ISO8601DateFormatter().string(from: Date()), "items": try rows(bound.list),
            "diagnostics": ["scope": "loaded target-group Accessibility rows only", "at_tail": atTail(bound.list) as Any? ?? NSNull(),
                            "window_restored": bound.binding["window_restored"] ?? false,
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
    return [CGEventType.mouseMoved, .leftMouseDown, .rightMouseDown, .otherMouseDown,
            .leftMouseDragged, .rightMouseDragged, .scrollWheel, .keyDown].map {
        CGEventSource.secondsSinceLastEventType(.combinedSessionState, eventType: $0)
    }.min() ?? 0
}

// Pointer motion does not take ownership of a read/AX-close operation. Clicks,
// drags, scrolling, typing and application switches still stop automation.
func controlIdleSeconds() -> Double {
    return [CGEventType.leftMouseDown, .rightMouseDown, .otherMouseDown,
            .leftMouseDragged, .rightMouseDragged, .otherMouseDragged, .scrollWheel, .keyDown].map {
        CGEventSource.secondsSinceLastEventType(.combinedSessionState, eventType: $0)
    }.min() ?? 0
}

// Stream tail recovery and one-shot expansion run in different processes.
// Serialize their entire notice/activation/return sequence, across groups too.
final class UIActionLease {
    private var descriptor: Int32
    init?() {
        descriptor = open(NSTemporaryDirectory() + "wechat-tracker-ui.lock", O_CREAT | O_RDWR | O_NOFOLLOW, 0o600)
        if descriptor < 0 { return nil }
        if flock(descriptor, LOCK_EX | LOCK_NB) != 0 {
            close(descriptor)
            descriptor = -1
            return nil
        }
    }
    func release() {
        if descriptor >= 0 { flock(descriptor, LOCK_UN); close(descriptor); descriptor = -1 }
    }
    deinit { release() }
}

final class ForwardNoticePanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}
final class ForwardNoticeChoice: NSObject {
    var postponed = false
    @objc func postpone(_ sender: Any?) { postponed = true }
}

// Deliberate input or an app switch cancels before any WeChat UI action. AppKit can
// deliver its own delayed activation while initializing an accessory process;
// restore only that exact self-activation while input remains idle.
func forwardNotice(foreground: pid_t, purpose: String? = nil) throws -> Int {
    let seconds = max(5, min(15, Int(option("--forward-notice-seconds", "5")) ?? 5))
    let postpone = max(60, min(3600, Int(option("--forward-postpone-seconds", "300")) ?? 300))
    let app = NSApplication.shared
    let launchTime = Date()
    let launchIdle = controlIdleSeconds()
    var selfActivations = 0
    func preserveNoticeFocus() throws {
        let current = NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0
        if current == foreground { return }
        if current != ProcessInfo.processInfo.processIdentifier { try fail("user_active_during_notice") }
        if selfActivations >= 2 || controlIdleSeconds() + 0.25 < launchIdle + Date().timeIntervalSince(launchTime) {
            try fail("user_active_during_notice")
        }
        guard let previous = NSRunningApplication(processIdentifier: foreground), let url = previous.bundleURL else {
            try fail("notice_activation_changed")
        }
        selfActivations += 1
        app.deactivate()
        let configuration = NSWorkspace.OpenConfiguration()
        configuration.activates = true
        configuration.addsToRecentItems = false
        NSWorkspace.shared.openApplication(at: url, configuration: configuration) { _, _ in }
        let deadline = Date().addingTimeInterval(2)
        while NSWorkspace.shared.frontmostApplication?.processIdentifier != foreground && Date() < deadline {
            CFRunLoopRunInMode(.defaultMode, 0.02, true)
        }
        if NSWorkspace.shared.frontmostApplication?.processIdentifier != foreground { try fail("notice_activation_changed") }
    }
    // The notice must run inside the installed LSUIElement app bundle. A raw
    // command-line helper can take focus when finishLaunching is called.
    guard Bundle.main.object(forInfoDictionaryKey: "LSUIElement") as? Bool == true else {
        try fail("notice_requires_ui_element_bundle")
    }
    if app.activationPolicy() != .accessory && !app.setActivationPolicy(.accessory) {
        try fail("notice_initialization_failed")
    }
    app.finishLaunching()
    try preserveNoticeFocus()
    guard let screen = NSScreen.screens.first(where: { $0.frame.contains(NSEvent.mouseLocation) }) ?? NSScreen.main else {
        try fail("notice_screen_unavailable")
    }
    let frame = screen.visibleFrame
    let panel = ForwardNoticePanel(contentRect: NSRect(x: frame.maxX - 440, y: frame.minY + 24, width: 416, height: 148),
        styleMask: [.nonactivatingPanel, .titled, .fullSizeContentView], backing: .buffered, defer: false)
    panel.title = purpose == nil ? "微信转发读取预告" : "微信采集恢复预告"
    panel.titleVisibility = .hidden
    panel.titlebarAppearsTransparent = true
    panel.isReleasedWhenClosed = false
    panel.hidesOnDeactivate = false
    panel.level = .floating
    panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
    panel.becomesKeyOnlyIfNeeded = true
    let heading = NSTextField(labelWithString: "")
    heading.frame = NSRect(x: 20, y: 99, width: 376, height: 25)
    heading.font = .boldSystemFont(ofSize: 16)
    let description = NSTextField(wrappingLabelWithString: "将短暂切到微信\(purpose ?? "读取“\(group)”的转发卡片")，完成后返回。移动鼠标会等待空闲；点击“稍后”可延后。")
    description.frame = NSRect(x: 20, y: 47, width: 376, height: 46)
    description.font = .systemFont(ofSize: 13)
    let choice = ForwardNoticeChoice()
    let postponeLabel = postpone % 60 == 0 ? "\(postpone / 60) 分钟" : "\(postpone) 秒"
    let button = NSButton(title: "稍后（\(postponeLabel)）", target: choice, action: #selector(ForwardNoticeChoice.postpone(_:)))
    button.frame = NSRect(x: 252, y: 9, width: 146, height: 32)
    button.bezelStyle = .rounded
    button.focusRingType = .none
    panel.contentView?.addSubview(heading)
    panel.contentView?.addSubview(description)
    panel.contentView?.addSubview(button)
    let started = Date()
    let inputBefore = controlIdleSeconds()
    let deadline = started.addingTimeInterval(Double(seconds))
    panel.orderFrontRegardless()
    defer { panel.orderOut(nil); panel.close() }
    while Date() < deadline {
        heading.stringValue = "\(max(1, Int(ceil(deadline.timeIntervalSinceNow)))) 秒后\(purpose == nil ? "读取微信转发" : "恢复微信采集")"
        if let event = app.nextEvent(matching: .any, until: Date().addingTimeInterval(0.05), inMode: .default, dequeue: true) {
            app.sendEvent(event)
        }
        app.updateWindows()
        if choice.postponed { try fail("user_postponed") }
        // Keep the button available throughout the countdown. Pointer motion
        // alone waits for the normal idle guard instead of a long group pause.
        try preserveNoticeFocus()
    }
    if controlIdleSeconds() + 0.25 < inputBefore + Date().timeIntervalSince(started) {
        try fail("user_active_during_notice")
    }
    return seconds
}

// Capability inspection is passive. An advertised action is not proof that
// invoking it opens a card; callers must verify a newly created detail window.
func inspectForwardCards() throws -> [String: Any] {
    let bound = try bindGroup()
    let visible = try rows(bound.list)
    let elements = children(bound.list)
    var cards: [[String: Any]] = []
    for item in visible {
        guard let index = item["row_index"] as? Int, let raw = item["text"] as? String,
              raw.hasPrefix("聊天记录") || raw.hasPrefix("群聊的聊天记录") || raw.hasPrefix("[聊天记录]") else { continue }
        var actionNames: CFArray?
        let status = AXUIElementCopyActionNames(elements[index], &actionNames)
        var focusedSettable = DarwinBoolean(false)
        let focusedStatus = AXUIElementIsAttributeSettable(elements[index], kAXFocusedAttribute as CFString, &focusedSettable)
        cards.append(["row_index": index, "fully_visible": item["fully_visible"] ?? false,
                      "role": text(elements[index], kAXRoleAttribute),
                      "identifier": text(elements[index], kAXIdentifierAttribute),
                      "actions": actionNames as? [String] ?? [], "actions_status": status.rawValue,
                      "focus_settable": focusedStatus == .success && focusedSettable.boolValue,
                      "text_sha256": SHA256.hash(data: Data(raw.utf8)).map { String(format: "%02x", $0) }.joined(),
                      "preview_characters": raw.count])
    }
    return ["ok": true, "status": "ready", "group": group, "source": "macos_ax",
            "bundle_id": bundleID, "process_id": Int(bound.application.processIdentifier),
            "window_title": text(bound.window, kAXTitleAttribute), "group_binding": bound.binding,
            "observed_at": ISO8601DateFormatter().string(from: Date()), "cards": cards,
            "wechat_is_active": bound.application.isActive,
            "foreground_pid": Int(NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0),
            "idle_seconds": idleSeconds(),
            "read_only": true, "ui_actions_performed": false]
}

// Read an already-open detail explicitly selected by the user. This function
// has no UI actions: no activation, presses, scrolling, typing or closing.
func readOpenForward() throws -> [String: Any] {
    let requestData = FileHandle.standardInput.readDataToEndOfFile()
    guard requestData.count < 4096, let request = try JSONSerialization.jsonObject(with: requestData) as? [String: Any],
          let index = request["row_index"] as? Int, let expected = request["text_sha256"] as? String,
          let pid = request["process_id"] as? Int, let title = request["detail_title"] as? String else { try fail("invalid_forward_request") }
    if title != "群聊的聊天记录" && title.range(of: "^.+和.+的聊天记录$", options: .regularExpression) == nil { try fail("invalid_detail_title") }
    let bound = try bindGroup()
    let foregroundBefore = NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0
    let wechatWasActive = bound.application.isActive
    if Int(bound.application.processIdentifier) != pid { try fail("parent_changed") }
    let elements = children(bound.list)
    if index < 0 || index >= elements.count { try fail("parent_changed") }
    let raw = body(elements[index])
    let hash = SHA256.hash(data: Data(raw.utf8)).map { String(format: "%02x", $0) }.joined()
    if hash != expected || !raw.hasPrefix("聊天记录") && !raw.hasPrefix("群聊的聊天记录") && !raw.hasPrefix("[聊天记录]") { try fail("parent_changed") }
    let windows = attribute(bound.root, kAXWindowsAttribute) as? [AXUIElement] ?? []
    let details = windows.filter { text($0, kAXTitleAttribute) == title && (attribute($0, kAXMinimizedAttribute) as? Bool) != true }
    if details.count != 1 { try fail(details.isEmpty ? "detail_not_open" : "ambiguous_detail") }
    let list = try messageList(details[0], identifier: forwardListID)
    return ["ok": true, "status": "ready", "source": "macos_ax", "group": group, "bundle_id": bundleID,
            "process_id": pid, "window_title": title, "list_count": 1, "list_role": text(list, kAXRoleAttribute),
            "observed_at": ISO8601DateFormatter().string(from: Date()), "items": try rows(list),
            "diagnostics": ["wechat_was_active": wechatWasActive,
                            "foreground_pid_before": Int(foregroundBefore),
                            "foreground_pid_after": Int(NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0),
                            "history_complete": false, "attachments_downloaded": false],
            "binding": ["method": "explicit_manual_parent_id", "process_id": pid,
                        "parent_row_index": index, "parent_text_sha256": expected,
                        "detail_window_count": 1, "read_only": true, "ui_actions_performed": false]]
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
    let list = try messageList(detail, identifier: forwardListID)
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

// Opt-in unattended expansion. This route briefly uses the foreground and a
// real mouse click; it must never be described as focus-free background input.
func closeIfStillOpen(_ detail: AXUIElement, root: AXUIElement) throws {
    if (attribute(root, kAXWindowsAttribute) as? [AXUIElement] ?? []).contains(where: { CFEqual($0, detail) }) {
        try closeOwnedDetail(detail, root: root)
    }
}

func closeOwnedDetail(_ detail: AXUIElement, root: AXUIElement) throws {
    guard let button = attribute(detail, kAXCloseButtonAttribute),
          CFGetTypeID(button as CFTypeRef) == AXUIElementGetTypeID() else { try fail("close_not_supported") }
    if AXUIElementPerformAction(button as! AXUIElement, kAXPressAction as CFString) != .success {
        try fail("detail_close_failed")
    }
    // Qt removes its AX window asynchronously; a fixed 150 ms sleep can reject
    // a successful close on a busy desktop. Verify disappearance with a bound.
    let deadline = Date().addingTimeInterval(1.5)
    while (attribute(root, kAXWindowsAttribute) as? [AXUIElement] ?? []).contains(where: { CFEqual($0, detail) }) {
        if Date() >= deadline { try fail("return_not_verified") }
        CFRunLoopRunInMode(.defaultMode, 0.04, true)
    }
}

func expandActivated() throws -> [String: Any] {
    let requestData = FileHandle.standardInput.readDataToEndOfFile()
    guard requestData.count < 4096,
          let request = try JSONSerialization.jsonObject(with: requestData) as? [String: Any],
          let index = request["row_index"] as? Int, let expected = request["text_sha256"] as? String,
          let pid = request["process_id"] as? Int, let title = request["detail_title"] as? String else { try fail("invalid_expansion_request") }
    if title != "群聊的聊天记录" && title.range(of: "^.+和.+的聊天记录$", options: .regularExpression) == nil { try fail("invalid_detail_title") }
    let side = option("--forward-card-side", "right")
    if side != "left" && side != "right" { try fail("invalid_forward_card_side") }
    let idleRequired = max(3, min(60, Double(option("--forward-idle-seconds", "3")) ?? 3))
    guard let previous = NSWorkspace.shared.frontmostApplication, previous.processIdentifier > 0 else { try fail("foreground_unknown") }
    let bound = try bindGroup(recover: true, recoveryPurpose: "读取“\(group)”的转发卡片")
    guard let lease = UIActionLease() else { try fail("user_busy") }
    defer { lease.release() }
    if NSWorkspace.shared.frontmostApplication?.processIdentifier != previous.processIdentifier { try fail("user_interrupted") }
    if Int(bound.application.processIdentifier) != pid { try fail("parent_changed") }
    if text(bound.window, kAXTitleAttribute) != group { try fail("activated_forward_requires_independent_window") }

    func parentRow() throws -> AXUIElement {
        let elements = children(bound.list)
        if index < 0 || index >= elements.count { try fail("parent_changed") }
        let row = elements[index]
        let raw = body(row)
        let hash = SHA256.hash(data: Data(raw.utf8)).map { String(format: "%02x", $0) }.joined()
        if hash != expected || text(row, kAXIdentifierAttribute) != "chat_bubble_item_view" ||
            (!raw.hasPrefix("聊天记录") && !raw.hasPrefix("群聊的聊天记录") && !raw.hasPrefix("[聊天记录]")) { try fail("parent_changed") }
        return row
    }
    _ = try parentRow()
    if idleSeconds() < idleRequired { try fail("user_busy") }
    let noticeSeconds: Int
    if let completed = bound.binding["recovery_notice_seconds"] as? Int { noticeSeconds = completed }
    else { noticeSeconds = try forwardNotice(foreground: previous.processIdentifier) }
    if controlIdleSeconds() < idleRequired { try fail("user_busy") }
    _ = try parentRow()
    let foregroundBefore = previous.processIdentifier
    let wasActive = bound.application.isActive
    let started = Date()
    var interrupted = false
    var restoreOnExit = true

    func foregroundPID() -> pid_t { NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0 }
    func activate(_ app: NSRunningApplication) throws {
        if foregroundPID() == app.processIdentifier { return }
        guard let url = app.bundleURL else { try fail("activation_url_missing") }
        let config = NSWorkspace.OpenConfiguration()
        config.activates = true
        config.addsToRecentItems = false
        NSWorkspace.shared.openApplication(at: url, configuration: config) { _, _ in }
        let deadline = Date().addingTimeInterval(3)
        while foregroundPID() != app.processIdentifier && Date() < deadline {
            CFRunLoopRunInMode(.defaultMode, 0.04, true)
        }
        if foregroundPID() != app.processIdentifier { try fail("activation_failed") }
    }
    // Restore only while WeChat still owns focus. A user switching apps takes
    // ownership; never switch them back after that interruption.
    defer {
        if restoreOnExit && !interrupted && foregroundPID() == bound.application.processIdentifier {
            try? activate(previous)
        }
    }
    try activate(bound.application)
    if AXUIElementPerformAction(bound.window, kAXRaiseAction as CFString) != .success { try fail("group_window_raise_failed") }
    CFRunLoopRunInMode(.defaultMode, 0.12, true)
    let rebound = try bindGroup()
    if !CFEqual(rebound.window, bound.window) { try fail("parent_changed") }
    let row = try parentRow()
    let baseline = attribute(bound.root, kAXWindowsAttribute) as? [AXUIElement] ?? []
    if baseline.contains(where: { text($0, kAXTitleAttribute) == title }) { try fail("detail_already_open") }
    var inputClock: Date?
    var idleAfterOwnInput: Double?
    var detail: AXUIElement?
    defer {
        // This defer also covers errors before the new window was assigned,
        // including pointer movement immediately after our synthetic click.
        if !interrupted, let clock = inputClock, let idle = idleAfterOwnInput,
           foregroundPID() == bound.application.processIdentifier,
           controlIdleSeconds() + 0.25 >= idle + Date().timeIntervalSince(clock) {
            let owned = (attribute(bound.root, kAXWindowsAttribute) as? [AXUIElement] ?? []).filter { candidate in
                text(candidate, kAXTitleAttribute) == title && !baseline.contains(where: { CFEqual($0, candidate) })
            }
            if let opened = detail { try? closeIfStillOpen(opened, root: bound.root) }
            else if owned.count == 1 { try? closeIfStillOpen(owned[0], root: bound.root) }
        }
    }
    if foregroundPID() != bound.application.processIdentifier || controlIdleSeconds() < idleRequired {
        interrupted = true
        try fail("user_interrupted")
    }
    guard let cardRect = rectangle(row), let viewport = rectangle(bound.list) else { try fail("geometry_unavailable") }
    let hit = cardRect.intersection(viewport)
    if hit.isNull || hit.width < 40 || hit.height < 24 { try fail("forward_card_clipped") }
    let inset = min(140, hit.width * 0.25)
    let point = CGPoint(x: side == "right" ? hit.maxX - inset : hit.minX + inset,
                        y: hit.minY + min(40, hit.height * 0.5))
    // Different WeChat windows can have identical frames. Resolve the actual
    // on-screen element and require ancestry back to this verified parent row.
    var actual: AXUIElement?
    if AXUIElementCopyElementAtPosition(AXUIElementCreateSystemWide(), Float(point.x), Float(point.y), &actual) != .success {
        try fail("card_occluded")
    }
    var current = actual
    var parentHit = false
    for _ in 0..<12 {
        guard let element = current else { break }
        if CFEqual(element, row) { parentHit = true; break }
        guard let parent = attribute(element, kAXParentAttribute), CFGetTypeID(parent as CFTypeRef) == AXUIElementGetTypeID() else { break }
        current = (parent as! AXUIElement)
    }
    if !parentHit { try fail("card_occluded") }
    let cursorBefore = CGEvent(source: nil)?.location ?? .zero
    let source = CGEventSource(stateID: .privateState)
    guard let down = CGEvent(mouseEventSource: source, mouseType: .leftMouseDown, mouseCursorPosition: point, mouseButton: .left),
          let up = CGEvent(mouseEventSource: source, mouseType: .leftMouseUp, mouseCursorPosition: point, mouseButton: .left) else { try fail("event_creation_failed") }
    down.setIntegerValueField(.mouseEventClickState, value: 1)
    up.setIntegerValueField(.mouseEventClickState, value: 1)
    down.post(tap: .cghidEventTap)
    Thread.sleep(forTimeInterval: 0.025)
    up.post(tap: .cghidEventTap)
    Thread.sleep(forTimeInterval: 0.04)
    let cursorAfter = CGEvent(source: nil)?.location ?? .zero
    let canRestoreCursor = hypot(cursorAfter.x-point.x, cursorAfter.y-point.y) < 2
    if canRestoreCursor { CGWarpMouseCursorPosition(cursorBefore) }
    CFRunLoopRunInMode(.defaultMode, 0.12, true)
    inputClock = Date()
    idleAfterOwnInput = controlIdleSeconds()
    let finalCursor = CGEvent(source: nil)?.location ?? .zero
    let cursorRestored = hypot(finalCursor.x-cursorBefore.x, finalCursor.y-cursorBefore.y) < 2
    if !cursorRestored {
        // Do not warp a pointer the user has moved. The failure cleanup can
        // still close our own detail if no click/key/app takeover occurred.
        try fail("user_busy")
    }
    func checkControl() throws {
        CFRunLoopRunInMode(.defaultMode, 0.001, true)
        let elapsed = Date().timeIntervalSince(inputClock!)
        if foregroundPID() != bound.application.processIdentifier ||
            (elapsed > 0.3 && controlIdleSeconds() + 0.25 < idleAfterOwnInput! + elapsed) {
            interrupted = true
            try fail("user_interrupted")
        }
    }
    let deadline = Date().addingTimeInterval(4)
    while Date() < deadline {
        try checkControl()
        let new = (attribute(bound.root, kAXWindowsAttribute) as? [AXUIElement] ?? []).filter { candidate in
            text(candidate, kAXTitleAttribute) == title && !baseline.contains(where: { CFEqual($0, candidate) })
        }
        if new.count > 1 { try fail("ambiguous_detail") }
        if new.count == 1 { detail = new[0]; break }
        CFRunLoopRunInMode(.defaultMode, 0.05, true)
    }
    guard let detail = detail else { try fail("detail_open_timeout") }
    let list = try messageList(detail, identifier: forwardListID)
    let listRole = text(list, kAXRoleAttribute)
    let items = try rows(list)
    if items.isEmpty { try fail("detail_body_not_loaded") }
    try checkControl()
    try closeOwnedDetail(detail, root: bound.root)
    try checkControl()
    let after = attribute(bound.root, kAXWindowsAttribute) as? [AXUIElement] ?? []
    if after.contains(where: { CFEqual($0, detail) }) { try fail("return_not_verified") }
    if !CFEqual(try bindGroup().window, bound.window) { try fail("return_not_verified") }
    try activate(previous)
    restoreOnExit = false
    let foregroundAfter = foregroundPID()
    return ["ok": true, "status": "ready", "group": group, "source": "macos_ax", "bundle_id": bundleID,
            "process_id": pid, "window_title": title, "list_count": 1, "list_role": listRole,
            "observed_at": ISO8601DateFormatter().string(from: Date()), "items": items,
            "diagnostics": ["history_complete": false, "attachments_downloaded": false,
                            "interaction_seconds": Date().timeIntervalSince(started)],
            "binding": ["process_id": pid, "parent_text_sha256": expected, "parent_row_index": index,
                        "new_window_observed": true, "restored": true,
                        "interaction_method": "activate_click", "card_side": side,
                        "hit_test": "verified_parent_row",
                        "activation_requested": true, "global_input_used": true,
                        "notice_completed": true, "notice_seconds": noticeSeconds,
                        "wechat_was_active": wasActive, "cursor_restored": cursorRestored,
                        "foreground_pid_before": Int(foregroundBefore), "foreground_pid_after": Int(foregroundAfter),
                        "foreground_restored": foregroundBefore == foregroundAfter]]
}

func credential(_ mode: String) throws {
    if mode == "credential-get" { SecKeychainSetUserInteractionAllowed(false) }
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
        emit(["ok": true, "version": "1.1.1", "architecture": architecture, "desktop_locked": desktopLocked(),
              "os": ProcessInfo.processInfo.operatingSystemVersionString])
    } else if action == "self-test-ui-lock" {
        guard let first = UIActionLease() else { try fail("ui_lock_busy") }
        let second = UIActionLease()
        let excluded = second == nil
        second?.release()
        first.release()
        let third = UIActionLease()
        let released = third != nil
        third?.release()
        emit(["ok": excluded && released, "mutual_exclusion": excluded, "release_verified": released,
              "wechat_ui_actions_performed": false])
    } else if action == "inspect-windows" {
        let apps = NSRunningApplication.runningApplications(withBundleIdentifier: bundleID)
        let values: [[String: Any]] = apps.map { application in
            var raw: CFTypeRef?
            let error = AXUIElementCopyAttributeValue(AXUIElementCreateApplication(application.processIdentifier), kAXWindowsAttribute as CFString, &raw)
            let windows = raw as? [AXUIElement] ?? []
            return ["process_id": Int(application.processIdentifier), "ax_error": error.rawValue,
                    "hidden": application.isHidden, "active": application.isActive,
                    "windows": windows.map { ["title": text($0, kAXTitleAttribute), "minimized": attribute($0, kAXMinimizedAttribute) as? Bool ?? false] }]
        }
        let cgWindows = (CGWindowListCopyWindowInfo(.optionAll, kCGNullWindowID) as? [[String: Any]] ?? []).filter {
            let owner = $0[kCGWindowOwnerName as String] as? String ?? ""
            return owner.hasPrefix("WeChat") || owner.hasPrefix("微信")
        }.map { window -> [String: Any] in
            let pid = (window[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value ?? 0
            let app = NSRunningApplication(processIdentifier: pid)
            return ["process_id": Int(pid), "owner": window[kCGWindowOwnerName as String] ?? "",
                    "title": window[kCGWindowName as String] ?? "", "bundle_id": app?.bundleIdentifier ?? "",
                    "executable": app?.executableURL?.path ?? "", "onscreen": window[kCGWindowIsOnscreen as String] ?? false]
        }
        emit(["ok": true, "accessibility_trusted": AXIsProcessTrusted(), "desktop_locked": desktopLocked(),
              "foreground_pid": Int(NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0),
              "processes": values, "window_server": cgWindows, "ui_actions_performed": false])
    } else if action == "authorize" {
        let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
        emit(["ok": AXIsProcessTrustedWithOptions(options), "status": "check_system_settings_accessibility"])
    } else if action.hasPrefix("credential-") {
        try credential(action)
    } else if action == "read-forward" {
        emit(try readOpenForward())
    } else if action == "inspect-forward" {
        emit(try inspectForwardCards())
    } else if action == "preview-forward-notice" {
        let before = NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0
        do {
            let seconds = try forwardNotice(foreground: before)
            emit(["ok": true, "status": "notice_completed", "group": group, "notice_seconds": seconds,
              "foreground_pid_before": Int(before),
              "foreground_pid_after": Int(NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0),
              "wechat_ui_actions_performed": false])
        } catch let error as Failure {
            emit(["ok": false, "status": error.code, "group": group,
                  "foreground_pid_before": Int(before),
                  "foreground_pid_after": Int(NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0),
                  "notice_process_id": Int(ProcessInfo.processInfo.processIdentifier),
                  "wechat_ui_actions_performed": false])
        }
    } else if action == "expand" {
        emit(try expand())
    } else if action == "expand-activated" {
        emit(try expandActivated())
    } else if action == "stream" {
        var last = ""
        var lastRead = Date.distantPast
        while getppid() == originalParent && originalParent > 1 {
            if (changed && Date().timeIntervalSince(lastRead) >= 0.3) || Date().timeIntervalSince(lastRead) >= resync {
                changed = false
                lastRead = Date()
                do {
                    let bound = try bindGroup(recover: recoverWindow)
                    let tail = try followLatest(bound)
                    let eventBound = subscribe(bound)
                    var result = try snapshot(bindGroup())
                    var diagnostics = result["diagnostics"] as? [String: Any] ?? [:]
                    diagnostics["tail_follow"] = tail
                    diagnostics["window_recovery_enabled"] = recoverWindow
                    result["diagnostics"] = diagnostics
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
        let bound = try bindGroup(recover: action == "capture" && recoverWindow)
        let beforeFollow = arguments.contains("--diagnostic-viewport") ? try rows(bound.list) : []
        let tail = action == "capture" ? try followLatest(bound) : [:]
        var result = try snapshot(bindGroup())
        var diagnostics = result["diagnostics"] as? [String: Any] ?? [:]
        diagnostics["tail_follow"] = tail
        if arguments.contains("--diagnostic-viewport") { diagnostics["viewport_before_follow"] = beforeFollow }
        result["diagnostics"] = diagnostics
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
