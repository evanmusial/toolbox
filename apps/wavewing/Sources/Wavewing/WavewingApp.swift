import AppKit
import Combine
import SwiftUI

@MainActor
@main
enum WavewingMain {
    private static let delegate = RadioAppDelegate()

    static func main() {
        let application = NSApplication.shared
        application.setActivationPolicy(.regular)
        application.delegate = delegate
        application.run()
    }
}

@MainActor
final class RadioAppDelegate: NSObject, NSApplicationDelegate {
    private let store = RadioStore()
    private var overlay: RadioOverlayController?
    private var settingsWindow: NSWindow?
    private var statusItem: NSStatusItem?
    private var visibilitySubscription: AnyCancellable?

    func applicationDidFinishLaunching(_ notification: Notification) {
        installStatusItem()
        overlay = RadioOverlayController(store: store) { [weak self] in self?.showSettings(nil) }
        visibilitySubscription = Publishers.CombineLatest(store.$hudVisible, store.$hasReadyMetadata)
            .sink { [weak self] visible, ready in
            if visible && ready { self?.overlay?.show() }
            else { self?.overlay?.hide() }
        }
        store.start()
    }

    func applicationWillTerminate(_ notification: Notification) {
        store.stop()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        showSettings(nil)
        return true
    }

    private func installStatusItem() {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let symbol = NSImage(systemSymbolName: "music.note", accessibilityDescription: "Wavewing") {
            item.button?.image = symbol
        } else {
            item.button?.title = "♫"
        }
        let menu = NSMenu()
        let settings = NSMenuItem(title: "Wavewing Settings…", action: #selector(showSettings(_:)), keyEquivalent: "")
        settings.target = self
        menu.addItem(settings)
        let toggle = NSMenuItem(title: "Toggle Left HUD", action: #selector(toggleHUD(_:)), keyEquivalent: "")
        toggle.target = self
        menu.addItem(toggle)
        menu.addItem(.separator())
        let quit = NSMenuItem(title: "Quit Wavewing", action: #selector(quit(_:)), keyEquivalent: "q")
        quit.target = self
        menu.addItem(quit)
        item.menu = menu
        statusItem = item
    }

    @objc private func showSettings(_ sender: Any?) {
        if settingsWindow == nil {
            let window = NSWindow(
                contentRect: NSRect(x: 0, y: 0, width: 590, height: 650),
                styleMask: [.titled, .closable, .miniaturizable, .resizable],
                backing: .buffered,
                defer: false
            )
            window.title = "Wavewing"
            window.minSize = NSSize(width: 430, height: 440)
            window.isReleasedWhenClosed = false
            window.contentView = NSHostingView(rootView: RadioSettingsView(store: store))
            window.center()
            settingsWindow = window
        }
        settingsWindow?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    @objc private func toggleHUD(_ sender: Any?) {
        store.hudVisible.toggle()
    }

    @objc private func quit(_ sender: Any?) {
        NSApp.terminate(nil)
    }
}
