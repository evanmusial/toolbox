import AppKit
import SwiftUI

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, ObservableObject {
    let store = QuotaStore()
    @Published private(set) var notchVisible = true
    private var notchOverlay: NotchOverlayController?

    func applicationDidFinishLaunching(_ notification: Notification) {
        if let iconURL = Bundle.main.url(forResource: "AppIcon", withExtension: "icns"),
           let icon = NSImage(contentsOf: iconURL) {
            NSApp.applicationIconImage = icon
        }
        store.connect()
        notchOverlay = NotchOverlayController(store: store)
        notchOverlay?.show()
    }

    func applicationWillTerminate(_ notification: Notification) {
        store.disconnect()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }

    func setNotchVisible(_ visible: Bool) {
        notchVisible = visible
        if visible {
            notchOverlay?.show()
        } else {
            notchOverlay?.hide()
        }
    }
}

@main
@MainActor
struct CodexMeterApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate

    var body: some Scene {
        WindowGroup(id: "dashboard") {
            DashboardView(store: appDelegate.store)
        }
        .windowStyle(.hiddenTitleBar)
        .defaultSize(width: 840, height: 420)
        .commands {
            CommandGroup(replacing: .newItem) { }
        }

        MenuBarExtra {
            MenuBarContentView(store: appDelegate.store, appDelegate: appDelegate)
        } label: {
            MenuBarGlyph()
        }
        .menuBarExtraStyle(.menu)
    }
}
