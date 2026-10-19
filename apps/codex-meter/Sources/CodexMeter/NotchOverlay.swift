import AppKit
import SwiftUI

private enum CodexBrandIcon {
    static let image: NSImage = {
        let bundled = Bundle.main.path(forResource: "CodexBrandIcon", ofType: "png")
        let installed = "/Applications/ChatGPT.app/Contents/Resources/icon-codex-dark-color.png"

        if let bundled, let image = NSImage(contentsOfFile: bundled) {
            return image
        }
        if let image = NSImage(contentsOfFile: installed) {
            return image
        }
        return NSImage(systemSymbolName: "terminal.fill", accessibilityDescription: "Codex") ?? NSImage()
    }()
}

@MainActor
final class NotchOverlayState: ObservableObject {
    @Published var isExpanded = false {
        didSet { expansionChanged?(isExpanded) }
    }

    var expansionChanged: ((Bool) -> Void)?
}

@MainActor
final class NotchOverlayController {
    private let store: QuotaStore
    private let overlayState = NotchOverlayState()
    private let panel: NSPanel
    private var screenObserver: NSObjectProtocol?

    private let collapsedSideWidth: CGFloat = 132
    private let expandedSideWidth: CGFloat = 190
    private let expandedBodyHeight: CGFloat = 114

    init(store: QuotaStore) {
        self.store = store
        panel = NSPanel(
            contentRect: .zero,
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )

        configurePanel()
        overlayState.expansionChanged = { [weak self] expanded in
            self?.updateFrame(expanded: expanded, animated: true)
        }

        screenObserver = NotificationCenter.default.addObserver(
            forName: NSApplication.didChangeScreenParametersNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in
                self?.updateContent()
                self?.updateFrame(expanded: self?.overlayState.isExpanded ?? false, animated: false)
            }
        }
    }

    deinit {
        if let screenObserver {
            NotificationCenter.default.removeObserver(screenObserver)
        }
    }

    func show() {
        updateContent()
        updateFrame(expanded: false, animated: false)
        panel.orderFrontRegardless()
    }

    func hide() {
        panel.orderOut(nil)
    }

    private func configurePanel() {
        panel.level = .statusBar
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
        panel.backgroundColor = .clear
        panel.isOpaque = false
        panel.hasShadow = false
        panel.hidesOnDeactivate = false
        panel.isMovable = false
        panel.isReleasedWhenClosed = false
        panel.becomesKeyOnlyIfNeeded = true
    }

    private func updateContent() {
        let screen = preferredScreen()
        let view = NotchOverlayView(
            store: store,
            overlayState: overlayState,
            notchGap: notchGap(for: screen),
            compactHeight: compactHeight(for: screen)
        )
        let hostingView = NSHostingView(rootView: view)
        hostingView.autoresizingMask = [.width, .height]
        panel.contentView = hostingView
    }

    private func updateFrame(expanded: Bool, animated: Bool) {
        let screen = preferredScreen()
        let gap = notchGap(for: screen)
        let sideWidth = expanded ? expandedSideWidth : collapsedSideWidth
        let width = gap + sideWidth * 2
        let compactHeight = compactHeight(for: screen)
        let height = expanded ? compactHeight + expandedBodyHeight : compactHeight
        let frame = NSRect(
            x: screen.frame.midX - width / 2,
            y: screen.frame.maxY - height,
            width: width,
            height: height
        )

        if animated {
            NSAnimationContext.runAnimationGroup { context in
                context.duration = 0.18
                context.timingFunction = CAMediaTimingFunction(name: .easeInEaseOut)
                panel.animator().setFrame(frame, display: true)
            }
        } else {
            panel.setFrame(frame, display: true)
        }
    }

    private func preferredScreen() -> NSScreen {
        NSScreen.screens.max { lhs, rhs in
            lhs.safeAreaInsets.top < rhs.safeAreaInsets.top
        } ?? NSScreen.main ?? NSScreen.screens[0]
    }

    private func notchGap(for screen: NSScreen) -> CGFloat {
        if let left = screen.auxiliaryTopLeftArea,
           let right = screen.auxiliaryTopRightArea {
            return min(220, max(120, right.minX - left.maxX))
        }
        return 16
    }

    private func compactHeight(for screen: NSScreen) -> CGFloat {
        let menuBarHeight = screen.frame.maxY - screen.visibleFrame.maxY
        let notchHeight = screen.safeAreaInsets.top
        return min(44, max(24, max(menuBarHeight, notchHeight)))
    }
}

private struct NotchOverlayView: View {
    @ObservedObject var store: QuotaStore
    @ObservedObject var overlayState: NotchOverlayState
    let notchGap: CGFloat
    let compactHeight: CGFloat

    var body: some View {
        VStack(spacing: 8) {
            compactRow

            if overlayState.isExpanded {
                expandedMetrics
                    .transition(.opacity.combined(with: .move(edge: .top)))
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .contentShape(Rectangle())
        .onHover { hovering in
            withAnimation(.easeInOut(duration: 0.16)) {
                overlayState.isExpanded = hovering
            }
        }
        .animation(.easeInOut(duration: 0.16), value: overlayState.isExpanded)
        .preferredColorScheme(.dark)
    }

    private var compactRow: some View {
        HStack(spacing: 0) {
            compactSide(alignment: .trailing) {
                HStack(spacing: 6) {
                    Image(nsImage: CodexBrandIcon.image)
                        .resizable()
                        .interpolation(.high)
                        .frame(width: 16, height: 16)
                    HStack(alignment: .firstTextBaseline, spacing: 3) {
                        Text("\(remainingPercent)%")
                            .font(.system(size: 12, weight: .semibold, design: .rounded))
                            .monospacedDigit()
                        Text("left")
                            .font(.system(size: 11, weight: .regular, design: .rounded))
                    }
                }
                .padding(.trailing, 8)
            }

            Color.clear.frame(width: notchGap)

            compactSide(alignment: .leading) {
                HStack(alignment: .firstTextBaseline, spacing: 3) {
                    Text(creditBalance)
                        .font(.system(size: 12, weight: .semibold, design: .rounded))
                        .monospacedDigit()
                    Text("credits")
                        .font(.system(size: 11, weight: .regular, design: .rounded))
                }
                .foregroundStyle(CodexPalette.accent)
            }
        }
        .frame(height: compactHeight)
        .background(
            overlayState.isExpanded
                ? Color(red: 0.018, green: 0.025, blue: 0.045).opacity(0.98)
                : Color(red: 0.018, green: 0.025, blue: 0.045).opacity(0.80)
        )
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
    }

    private func compactSide<Content: View>(
        alignment: HorizontalAlignment,
        @ViewBuilder content: () -> Content
    ) -> some View {
        VStack(alignment: alignment, spacing: 1, content: content)
            .foregroundStyle(CodexPalette.text)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: alignment == .leading ? .leading : .trailing)
            .padding(.horizontal, 12)
    }

    private var expandedMetrics: some View {
        HStack(spacing: 0) {
            NotchMetric(value: "\(remainingPercent)%", label: "Remaining", tint: remainingTint)
            notchDivider
            NotchMetric(value: expandedReset, label: "Resets")
            notchDivider
            NotchMetric(value: creditBalance, label: "Credits", tint: CodexPalette.accentLight)
        }
        .padding(.horizontal, 8)
        .frame(height: 106)
        .background(CodexPalette.background.opacity(0.98))
        .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
        .shadow(color: .black.opacity(0.34), radius: 18, y: 8)
    }

    private var notchDivider: some View {
        Rectangle()
            .fill(CodexPalette.border)
            .frame(width: 1, height: 46)
    }

    private var remainingPercent: Int {
        store.snapshot?.primaryWindow?.remainingPercent ?? 0
    }

    private var remainingTint: Color {
        switch remainingPercent {
        case 40...: return CodexPalette.accentLight
        case 15..<40: return CodexPalette.warning
        default: return CodexPalette.danger
        }
    }

    private var resetDate: Date? {
        store.snapshot?.primaryWindow?.resetDate
    }

    private var expandedReset: String {
        guard let resetDate else { return "—" }
        return resetDate.formatted(.dateTime.month(.abbreviated).day().hour().minute())
    }

    private var creditBalance: String {
        guard let credits = store.snapshot?.buckets.first?.credits else { return "—" }
        if credits.unlimited { return "Unlimited" }
        return credits.formattedBalance ?? (credits.hasCredits ? "Available" : "0")
    }
}

private struct NotchMetric: View {
    let value: String
    let label: String
    var tint: Color = CodexPalette.text

    var body: some View {
        VStack(spacing: 4) {
            Text(value)
                .font(.system(size: 20, weight: .semibold, design: .rounded))
                .foregroundStyle(tint)
                .monospacedDigit()
                .lineLimit(1)
                .minimumScaleFactor(0.55)
            Text(label)
                .font(.system(size: 10, weight: .medium))
                .foregroundStyle(CodexPalette.secondaryText)
        }
        .frame(maxWidth: .infinity)
        .padding(.horizontal, 8)
    }
}
