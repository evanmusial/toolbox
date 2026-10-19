import AppKit
import SwiftUI

struct MenuBarGlyph: View {
    var body: some View {
        ZStack {
            Circle()
                .trim(from: 0.08, to: 0.82)
                .stroke(style: StrokeStyle(lineWidth: 1.6, lineCap: .round))
                .rotationEffect(.degrees(-90))
            Text(">_")
                .font(.system(size: 6.5, weight: .bold, design: .monospaced))
                .offset(y: 0.5)
        }
        .frame(width: 16, height: 16)
        .accessibilityLabel("Codex Meter")
    }
}

struct MenuBarContentView: View {
    @ObservedObject var store: QuotaStore
    @ObservedObject var appDelegate: AppDelegate
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        Text("\(remainingPercent)% left")
        Text("\(creditBalance) credits")

        Divider()

        Button("Show Dashboard") {
            openWindow(id: "dashboard")
            NSApp.activate(ignoringOtherApps: true)
        }

        Button("Refresh Now") {
            store.refresh()
        }

        Button(appDelegate.notchVisible ? "Hide Notch Display" : "Show Notch Display") {
            appDelegate.setNotchVisible(!appDelegate.notchVisible)
        }

        Divider()

        Button("Quit Codex Meter") {
            NSApp.terminate(nil)
        }
        .keyboardShortcut("q")
    }

    private var remainingPercent: Int {
        store.snapshot?.primaryWindow?.remainingPercent ?? 0
    }

    private var creditBalance: String {
        guard let credits = store.snapshot?.buckets.first?.credits else { return "—" }
        if credits.unlimited { return "Unlimited" }
        return credits.formattedBalance ?? (credits.hasCredits ? "Available" : "0")
    }
}
