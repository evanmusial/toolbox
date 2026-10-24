import AppKit
import SwiftUI

private final class CrispHostingView<Content: View>: NSHostingView<Content> {
    override var allowsVibrancy: Bool { false }
}

@MainActor
final class NotchOverlayState: ObservableObject {
    @Published var isExpanded = false {
        didSet { expansionChanged?(isExpanded) }
    }

    var expansionChanged: ((Bool) -> Void)?
    var shouldCollapse: (() -> Bool)?
    private var collapseWorkItem: DispatchWorkItem?

    func setHovering(_ hovering: Bool) {
        collapseWorkItem?.cancel()
        collapseWorkItem = nil

        if hovering {
            if !isExpanded { isExpanded = true }
        } else {
            scheduleCollapseCheck()
        }
    }

    private func scheduleCollapseCheck() {
        let workItem = DispatchWorkItem { [weak self] in
            guard let self else { return }
            if self.shouldCollapse?() ?? true {
                if self.isExpanded { self.isExpanded = false }
                self.collapseWorkItem = nil
            } else {
                self.scheduleCollapseCheck()
            }
        }
        collapseWorkItem = workItem
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.16, execute: workItem)
    }
}

@MainActor
final class NotchOverlayController {
    private let store: QuotaStore
    private let overlayState = NotchOverlayState()
    private let panel: NSPanel
    private weak var blurView: NSVisualEffectView?
    private var screenObserver: NSObjectProtocol?

    private let collapsedSideWidth: CGFloat = 132
    private let expandedSideWidth: CGFloat = 190
    private let expandedBodyHeight: CGFloat = 106

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
        overlayState.shouldCollapse = { [weak self] in
            guard let self else { return true }
            return !self.panel.frame.contains(NSEvent.mouseLocation)
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
        let hostingView = CrispHostingView(rootView: view)
        hostingView.translatesAutoresizingMaskIntoConstraints = false
        hostingView.wantsLayer = true
        hostingView.alphaValue = 1
        hostingView.layer?.backgroundColor = NSColor.clear.cgColor
        hostingView.layer?.isOpaque = false
        hostingView.layer?.allowsGroupOpacity = false
        hostingView.layer?.zPosition = 1

        let blurView = NSVisualEffectView()
        blurView.translatesAutoresizingMaskIntoConstraints = false
        blurView.material = .hudWindow
        blurView.blendingMode = .behindWindow
        blurView.state = .active
        blurView.isEmphasized = true
        blurView.appearance = NSAppearance(named: .darkAqua)
        blurView.alphaValue = 1
        blurView.wantsLayer = true
        blurView.layer?.backgroundColor = NSColor.clear.cgColor
        blurView.layer?.masksToBounds = true
        blurView.addSubview(hostingView)
        self.blurView = blurView

        NSLayoutConstraint.activate([
            hostingView.leadingAnchor.constraint(equalTo: blurView.leadingAnchor),
            hostingView.trailingAnchor.constraint(equalTo: blurView.trailingAnchor),
            hostingView.topAnchor.constraint(equalTo: blurView.topAnchor),
            hostingView.bottomAnchor.constraint(equalTo: blurView.bottomAnchor)
        ])
        panel.contentView = blurView
    }

    private func updateFrame(expanded: Bool, animated: Bool) {
        let screen = preferredScreen()
        let gap = notchGap(for: screen)
        let sideWidth = expanded ? expandedSideWidth : collapsedSideWidth
        let width = gap + sideWidth * 2
        let compactHeight = compactHeight(for: screen)
        let height = expanded ? compactHeight + expandedBodyHeight : compactHeight
        updateBlurMask(expanded: expanded)
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

    private func updateBlurMask(expanded: Bool) {
        guard let layer = blurView?.layer else { return }
        layer.cornerRadius = expanded ? 18 : 12
        layer.maskedCorners = expanded
            ? [.layerMinXMinYCorner, .layerMaxXMinYCorner]
            : [.layerMinXMinYCorner, .layerMaxXMinYCorner, .layerMinXMaxYCorner, .layerMaxXMaxYCorner]
        layer.masksToBounds = true
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
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var showResetAvailable = false
    let notchGap: CGFloat
    let compactHeight: CGFloat

    var body: some View {
        VStack(spacing: 0) {
            compactRow

            if overlayState.isExpanded {
                expandedMetrics
                    .transition(.opacity.combined(with: .move(edge: .top)))
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .contentShape(Rectangle())
        .onHover { hovering in
            overlayState.setHovering(hovering)
        }
        .task(id: resetAvailable) {
            showResetAvailable = false
            guard resetAvailable else { return }

            do {
                while !Task.isCancelled {
                    try await Task.sleep(nanoseconds: 25_000_000_000)
                    withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.34)) {
                        showResetAvailable = true
                    }
                    try await Task.sleep(nanoseconds: 5_000_000_000)
                    withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.34)) {
                        showResetAvailable = false
                    }
                }
            } catch {
                showResetAvailable = false
            }
        }
        .animation(.easeInOut(duration: 0.16), value: overlayState.isExpanded)
        .preferredColorScheme(.dark)
    }

    private var compactRow: some View {
        HStack(spacing: 0) {
            compactSide(alignment: .trailing) {
                ZStack(alignment: .trailing) {
                    HStack(alignment: .firstTextBaseline, spacing: 3) {
                        Text("Quota")
                            .font(.system(size: 11, weight: .regular, design: .rounded))
                        Text("\(remainingPercent)%")
                            .font(.system(size: 12, weight: .semibold, design: .rounded))
                            .monospacedDigit()
                        Text("left")
                            .font(.system(size: 11, weight: .regular, design: .rounded))
                    }
                    .offset(y: showResetAvailable ? -compactHeight : 0)
                    .opacity(showResetAvailable ? 0 : 1)

                    Text("Reset Available")
                        .font(.system(size: 12, weight: .regular, design: .rounded))
                        .foregroundStyle(CodexPalette.resetAvailable)
                        .offset(y: showResetAvailable ? 0 : compactHeight)
                        .opacity(showResetAvailable ? 1 : 0)
                }
                .frame(height: compactHeight)
                .clipped()
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
        .clipShape(
            RoundedRectangle(
                cornerRadius: overlayState.isExpanded ? 0 : 12,
                style: .continuous
            )
        )
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
            NotchMetric(value: creditBalance, label: "Credits", tint: CodexPalette.accent)
        }
        .padding(.horizontal, 8)
        .frame(height: 106)
        .background {
            Rectangle().fill(
                Color(red: 0.018, green: 0.025, blue: 0.045).opacity(0.40)
            )
        }
        .clipShape(BottomRoundedRectangle(radius: 18))
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

    private var resetAvailable: Bool {
        (store.snapshot?.rateLimitResetCredits?.availableCount ?? 0) > 0
    }

    private var remainingTint: Color {
        switch remainingPercent {
        case 40...: return CodexPalette.accent
        case 15..<40: return CodexPalette.resetAvailable
        default: return CodexPalette.quotaCritical
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

private struct BottomRoundedRectangle: Shape {
    let radius: CGFloat

    func path(in rect: CGRect) -> Path {
        var path = Path()
        path.move(to: CGPoint(x: rect.minX, y: rect.minY))
        path.addLine(to: CGPoint(x: rect.maxX, y: rect.minY))
        path.addLine(to: CGPoint(x: rect.maxX, y: rect.maxY - radius))
        path.addQuadCurve(
            to: CGPoint(x: rect.maxX - radius, y: rect.maxY),
            control: CGPoint(x: rect.maxX, y: rect.maxY)
        )
        path.addLine(to: CGPoint(x: rect.minX + radius, y: rect.maxY))
        path.addQuadCurve(
            to: CGPoint(x: rect.minX, y: rect.maxY - radius),
            control: CGPoint(x: rect.minX, y: rect.maxY)
        )
        path.closeSubpath()
        return path
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
