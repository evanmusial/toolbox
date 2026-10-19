import Combine
import Foundation

@MainActor
final class QuotaStore: ObservableObject {
    enum ConnectionState: Equatable {
        case disconnected
        case connecting
        case live

        var title: String {
            switch self {
            case .disconnected: return "Offline"
            case .connecting: return "Connecting"
            case .live: return "Live"
            }
        }
    }

    @Published private(set) var snapshot: RateLimitResponse?
    @Published private(set) var state: ConnectionState = .disconnected
    @Published private(set) var lastUpdated: Date?
    @Published private(set) var errorMessage: String?
    @Published private(set) var isRefreshing = false

    let refreshInterval: TimeInterval = 5
    private let client = CodexAppServerClient()
    private var refreshCancellable: AnyCancellable?

    func connect() {
        guard state == .disconnected else { return }
        state = .connecting
        isRefreshing = true

        do {
            try client.start(
                onSnapshot: { [weak self] snapshot in
                    DispatchQueue.main.async {
                        self?.apply(snapshot)
                    }
                },
                onError: { [weak self] error in
                    DispatchQueue.main.async {
                        self?.apply(error)
                    }
                }
            )
            startRefreshTimer()
        } catch {
            apply(error)
        }
    }

    func refresh() {
        if !client.isRunning {
            state = .disconnected
            connect()
            return
        }
        isRefreshing = true
        client.requestRateLimits()
    }

    func disconnect() {
        refreshCancellable?.cancel()
        refreshCancellable = nil
        client.stop()
        state = .disconnected
        isRefreshing = false
    }

    private func startRefreshTimer() {
        guard refreshCancellable == nil else { return }
        refreshCancellable = Timer.publish(every: refreshInterval, on: .main, in: .common)
            .autoconnect()
            .sink { [weak self] _ in
                self?.refresh()
            }
    }

    private func apply(_ snapshot: RateLimitResponse) {
        self.snapshot = snapshot
        state = .live
        lastUpdated = Date()
        errorMessage = nil
        isRefreshing = false
    }

    private func apply(_ error: Error) {
        errorMessage = error.localizedDescription
        state = .disconnected
        isRefreshing = false
    }
}
