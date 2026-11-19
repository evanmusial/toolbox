import AppKit
import ApplicationServices

struct MenuBarEdges {
    let leftMenuEnd: CGFloat?
    let rightExtrasStart: CGFloat?
}

enum MenuBarSpaceProbe {
    static var hasAccess: Bool { AXIsProcessTrusted() }

    @discardableResult
    static func requestAccess() -> Bool {
        let key = kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String
        return AXIsProcessTrustedWithOptions([key: true] as CFDictionary)
    }

    static func edges(screen: NSScreen, notchLeft: CGFloat, notchRight: CGFloat) -> MenuBarEdges {
        guard hasAccess else { return MenuBarEdges(leftMenuEnd: nil, rightExtrasStart: nil) }

        let owner = NSWorkspace.shared.menuBarOwningApplication ?? NSWorkspace.shared.frontmostApplication
        let leftFrames = owner.map { menuFrames(pid: $0.processIdentifier, attribute: kAXMenuBarAttribute) } ?? []
        let leftEnd = leftFrames
            .filter { $0.width > 0 && $0.minX >= screen.frame.minX - 1 && $0.maxX <= notchLeft + 1 }
            .map(\.maxX)
            .max()

        let systemUI = NSWorkspace.shared.runningApplications.first {
            $0.bundleIdentifier == "com.apple.systemuiserver"
        }
        var rightFrames = systemUI.map {
            menuFrames(pid: $0.processIdentifier, attribute: kAXExtrasMenuBarAttribute)
        } ?? []
        if rightFrames.isEmpty, let owner {
            rightFrames = menuFrames(pid: owner.processIdentifier, attribute: kAXExtrasMenuBarAttribute)
        }
        let rightStart = rightFrames
            .filter { $0.width > 0 && $0.minX >= notchRight - 1 && $0.maxX <= screen.frame.maxX + 1 }
            .map(\.minX)
            .min()
        return MenuBarEdges(leftMenuEnd: leftEnd, rightExtrasStart: rightStart)
    }

    private static func menuFrames(pid: pid_t, attribute: String) -> [CGRect] {
        let app = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(app, 0.15)
        guard let rawBar = copyAttribute(app, attribute),
              CFGetTypeID(rawBar) == AXUIElementGetTypeID() else { return [] }
        let bar = rawBar as! AXUIElement
        return children(of: bar).flatMap { child -> [CGRect] in
            if let direct = frame(of: child), direct.width > 0, direct.height < 80 {
                return [direct]
            }
            return children(of: child).compactMap(frame(of:))
        }
    }

    private static func children(of element: AXUIElement) -> [AXUIElement] {
        copyAttribute(element, kAXChildrenAttribute) as? [AXUIElement] ?? []
    }

    private static func frame(of element: AXUIElement) -> CGRect? {
        guard let rawPosition = copyAttribute(element, kAXPositionAttribute),
              let rawSize = copyAttribute(element, kAXSizeAttribute),
              CFGetTypeID(rawPosition) == AXValueGetTypeID(),
              CFGetTypeID(rawSize) == AXValueGetTypeID() else { return nil }
        let position = rawPosition as! AXValue
        let size = rawSize as! AXValue
        var point = CGPoint.zero
        var extent = CGSize.zero
        guard AXValueGetValue(position, .cgPoint, &point),
              AXValueGetValue(size, .cgSize, &extent) else { return nil }
        return CGRect(origin: point, size: extent)
    }

    private static func copyAttribute(_ element: AXUIElement, _ name: String) -> CFTypeRef? {
        var value: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success else { return nil }
        return value
    }
}
