import AppKit
import Combine
import SwiftUI

private final class SharpHostingView<Content: View>: NSHostingView<Content> {
    override var allowsVibrancy: Bool { false }
}

enum WingSide {
    case metadata
    case station
}

struct WingWidths {
    let left: CGFloat
    let right: CGFloat
}

enum WingLayout {
    static func fit(leftDesired: CGFloat, rightDesired: CGFloat,
                    leftCapacity: CGFloat, rightCapacity: CGFloat) -> WingWidths? {
        guard leftCapacity >= 80, rightCapacity >= 72 else { return nil }
        return WingWidths(
            left: min(leftCapacity, max(80, leftDesired)),
            right: min(rightCapacity, max(72, rightDesired))
        )
    }
}

@MainActor
final class RadioOverlayController {
    private let store: RadioStore
    private let leftPanel: NSPanel
    private let rightPanel: NSPanel
    private var screenObserver: NSObjectProtocol?
    private var appObserver: NSObjectProtocol?
    private var revisionSubscription: AnyCancellable?
    private var menuTimer: Timer?
    private var requestedVisible = false

    init(store: RadioStore, onClick: @escaping @MainActor () -> Void) {
        self.store = store
        leftPanel = Self.makePanel()
        rightPanel = Self.makePanel()
        leftPanel.contentView = Self.makeContent(store: store, side: .metadata, onClick: onClick)
        rightPanel.contentView = Self.makeContent(store: store, side: .station, onClick: onClick)

        screenObserver = NotificationCenter.default.addObserver(
            forName: NSApplication.didChangeScreenParametersNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in self?.updateFrames(animated: false) }
        }
        appObserver = NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.didActivateApplicationNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in self?.updateFrames(animated: true) }
        }
        revisionSubscription = store.$revision.dropFirst().sink { [weak self] _ in
            Task { @MainActor in self?.updateFrames(animated: true) }
        }
        let timer = Timer(timeInterval: 0.8, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.updateFrames(animated: true) }
        }
        RunLoop.main.add(timer, forMode: .common)
        menuTimer = timer
        updateFrames(animated: false)
    }

    deinit {
        if let screenObserver { NotificationCenter.default.removeObserver(screenObserver) }
        if let appObserver { NSWorkspace.shared.notificationCenter.removeObserver(appObserver) }
        menuTimer?.invalidate()
    }

    func show() {
        requestedVisible = true
        updateFrames(animated: false)
    }

    func hide() {
        requestedVisible = false
        leftPanel.orderOut(nil)
        rightPanel.orderOut(nil)
    }

    private static func makePanel() -> NSPanel {
        let panel = NSPanel(
            contentRect: .zero,
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        panel.level = NSWindow.Level(rawValue: NSWindow.Level.statusBar.rawValue + 1)
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
        panel.backgroundColor = .clear
        panel.isOpaque = false
        panel.hasShadow = false
        panel.hidesOnDeactivate = false
        panel.isMovable = false
        panel.isReleasedWhenClosed = false
        panel.becomesKeyOnlyIfNeeded = true
        return panel
    }

    private static func makeContent(store: RadioStore, side: WingSide,
                                    onClick: @escaping @MainActor () -> Void) -> NSView {
        let blur = NSVisualEffectView()
        blur.material = .hudWindow
        blur.blendingMode = .behindWindow
        blur.state = .active
        blur.appearance = NSAppearance(named: .darkAqua)
        blur.wantsLayer = true
        blur.layer?.cornerRadius = 12
        blur.layer?.masksToBounds = true

        let hosting = SharpHostingView(rootView: RadioWingView(store: store, side: side, onClick: onClick))
        hosting.translatesAutoresizingMaskIntoConstraints = false
        hosting.wantsLayer = true
        hosting.layer?.backgroundColor = NSColor.clear.cgColor
        blur.addSubview(hosting)
        NSLayoutConstraint.activate([
            hosting.leadingAnchor.constraint(equalTo: blur.leadingAnchor),
            hosting.trailingAnchor.constraint(equalTo: blur.trailingAnchor),
            hosting.topAnchor.constraint(equalTo: blur.topAnchor),
            hosting.bottomAnchor.constraint(equalTo: blur.bottomAnchor)
        ])
        return blur
    }

    private func updateFrames(animated: Bool) {
        guard let screen = NSScreen.screens.max(by: { $0.safeAreaInsets.top < $1.safeAreaInsets.top }) ?? NSScreen.main else {
            return
        }
        let gap: CGFloat
        if let left = screen.auxiliaryTopLeftArea, let right = screen.auxiliaryTopRightArea {
            gap = min(220, max(120, right.minX - left.maxX))
        } else {
            gap = 16
        }
        let notchLeft = screen.frame.midX - gap / 2
        let notchRight = screen.frame.midX + gap / 2
        let edges = MenuBarSpaceProbe.edges(screen: screen, notchLeft: notchLeft, notchRight: notchRight)
        // With no Accessibility access, keep both wings compact rather than guessing
        // how far other applications' menu items extend.
        let leftBoundary = edges.leftMenuEnd.map { max(screen.frame.minX + 8, $0 + 8) } ?? notchLeft - 220
        let rightBoundary = edges.rightExtrasStart.map { min(screen.frame.maxX - 8, $0 - 8) } ?? notchRight + 132
        let leftCapacity = notchLeft - leftBoundary
        let rightCapacity = rightBoundary - notchRight
        guard let widths = WingLayout.fit(
            leftDesired: desiredWidth(for: .metadata),
            rightDesired: desiredWidth(for: .station),
            leftCapacity: leftCapacity,
            rightCapacity: rightCapacity
        ) else {
            leftPanel.orderOut(nil)
            rightPanel.orderOut(nil)
            return
        }
        let menuBarHeight = screen.frame.maxY - screen.visibleFrame.maxY
        let height = min(44, max(24, max(menuBarHeight, screen.safeAreaInsets.top)))
        let leftFrame = NSRect(x: notchLeft - widths.left, y: screen.frame.maxY - height,
                               width: widths.left, height: height)
        let rightFrame = NSRect(x: notchRight, y: screen.frame.maxY - height,
                                width: widths.right, height: height)
        let changed = !leftPanel.frame.equalTo(leftFrame) || !rightPanel.frame.equalTo(rightFrame)
        if changed {
            if animated && leftPanel.isVisible && rightPanel.isVisible {
                NSAnimationContext.runAnimationGroup { context in
                    context.duration = 0.18
                    context.timingFunction = CAMediaTimingFunction(name: .easeInEaseOut)
                    leftPanel.animator().setFrame(leftFrame, display: true)
                    rightPanel.animator().setFrame(rightFrame, display: true)
                }
            } else {
                leftPanel.setFrame(leftFrame, display: true)
                rightPanel.setFrame(rightFrame, display: true)
            }
        }
        if requestedVisible {
            if !leftPanel.isVisible { leftPanel.orderFrontRegardless() }
            if !rightPanel.isVisible { rightPanel.orderFrontRegardless() }
        }
    }

    private func desiredWidth(for side: WingSide) -> CGFloat {
        let text: NSString
        let weight: NSFont.Weight
        switch side {
        case .metadata:
            text = store.displayedText as NSString
            weight = .regular
        case .station:
            text = (store.displayedStation?.name ?? "") as NSString
            weight = .bold
        }
        let width = text.size(withAttributes: [
            .font: NSFont.systemFont(ofSize: 11, weight: weight)
        ]).width
        return ceil(width + 28)
    }
}

private struct RadioWingView: View {
    @ObservedObject var store: RadioStore
    let side: WingSide
    let onClick: @MainActor () -> Void

    @State private var renderedText = ""
    @State private var opacity = 1.0
    @State private var horizontalOffset: CGFloat = 0
    @State private var highlighted = false
    @State private var animationTask: Task<Void, Never>?

    private let tint = Color(red: 0.51, green: 0.68, blue: 1.0)

    var body: some View {
        Text(renderedText)
            .font(.system(size: 11, weight: side == .station ? .bold : .regular, design: .rounded))
            .foregroundColor(highlighted && side == .station ? tint : .white)
            .lineLimit(1)
            .truncationMode(.tail)
            .frame(maxWidth: .infinity, alignment: side == .metadata ? .trailing : .leading)
            .opacity(opacity)
            .offset(x: horizontalOffset)
            .padding(.horizontal, 10)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(Color(red: 0.018, green: 0.025, blue: 0.045).opacity(0.84))
            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
            .contentShape(Rectangle())
            .onTapGesture { onClick() }
            .help(side == .metadata ? store.displayedText : (store.displayedStation?.name ?? ""))
            .accessibilityLabel(side == .metadata ? "Now playing: \(renderedText)" : "Station: \(renderedText)")
            .onAppear { setRenderedContent() }
            .onChange(of: store.revision) { _ in animateChange() }
            .onDisappear { animationTask?.cancel() }
            .preferredColorScheme(.dark)
    }

    private func setRenderedContent() {
        renderedText = side == .metadata ? store.displayedText : (store.displayedStation?.name ?? "")
    }

    private func animateChange() {
        animationTask?.cancel()
        if store.motion == .none {
            setRenderedContent()
            opacity = 1
            horizontalOffset = 0
            highlighted = false
            return
        }
        let fresh = store.motion == .glideWithTint
        animationTask = Task { @MainActor in
            withAnimation(.easeIn(duration: 0.13)) {
                opacity = 0
                horizontalOffset = fresh ? -16 : 0
            }
            try? await Task.sleep(nanoseconds: 130_000_000)
            guard !Task.isCancelled else { return }
            setRenderedContent()
            horizontalOffset = fresh ? 16 : 0
            highlighted = fresh && side == .station
            withAnimation(.easeOut(duration: 0.17)) {
                opacity = 1
                horizontalOffset = 0
            }
            if fresh {
                try? await Task.sleep(nanoseconds: 1_000_000_000)
                guard !Task.isCancelled else { return }
                withAnimation(.easeOut(duration: 0.3)) { highlighted = false }
            }
        }
    }
}
