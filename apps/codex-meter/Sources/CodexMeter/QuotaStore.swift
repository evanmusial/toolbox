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
    @Published private(set) var history: [UsageHistorySample] = []
    @Published private(set) var historySaveError: String?
    @Published private(set) var showingUsageRates = false
    @Published private(set) var cycleTiming: UsageCycleTiming
    @Published var trendLinesEnabled: Bool {
        didSet { defaults.set(trendLinesEnabled, forKey: "trendLinesEnabled") }
    }
    @Published var trendTimeRange: TrendTimeRange {
        didSet { defaults.set(trendTimeRange.rawValue, forKey: "trendTimeRange") }
    }

    let refreshInterval: TimeInterval = 5
    private let client = CodexAppServerClient()
    private let defaults: UserDefaults
    private let historyStorage: UsageHistoryStorage
    private var refreshCancellable: AnyCancellable?
    private var cycleCancellable: AnyCancellable?
    private var cycleStartedAt: TimeInterval = 0

    init(defaults: UserDefaults = .standard, historyStorage: UsageHistoryStorage = UsageHistoryStorage()) {
        self.defaults = defaults
        self.historyStorage = historyStorage
        cycleTiming = UsageCycleTiming(
            numbersSeconds: defaults.object(forKey: "numbersDisplaySeconds") as? Int ?? 15,
            ratesSeconds: defaults.object(forKey: "ratesDisplaySeconds") as? Int ?? 5
        )
        trendLinesEnabled = defaults.object(forKey: "trendLinesEnabled") as? Bool ?? true
        trendTimeRange = defaults.string(forKey: "trendTimeRange").flatMap(TrendTimeRange.init(rawValue:)) ?? .oneDay
    }

    var primaryQuotaTrend: TrendSeries? {
        guard let metric = primaryQuotaMetric else { return nil }
        return trend(for: metric)
    }

    var primaryQuotaRate: ActiveUsageRate? {
        guard let metric = primaryQuotaMetric else { return nil }
        return usageRate(for: metric)
    }

    var creditsRate: ActiveUsageRate? {
        guard let bucket = snapshot?.buckets.first else { return nil }
        return usageRate(for: .credits(bucketID: bucket.id))
    }

    private var primaryQuotaMetric: TrendMetric? {
        guard let snapshot else { return nil }
        let bucket = snapshot.buckets.first { $0.limitId == "codex" && $0.primary != nil }
            ?? snapshot.buckets.first { $0.primary != nil }
        return bucket.map { .quota(windowID: "\($0.id)-primary") }
    }

    func usageRate(for metric: TrendMetric) -> ActiveUsageRate? {
        ActiveUsageRate.make(samples: history, metric: metric, range: trendTimeRange, now: Date())
    }

    func setCycleTiming(numbersSeconds: Int, ratesSeconds: Int) {
        let timing = UsageCycleTiming(numbersSeconds: numbersSeconds, ratesSeconds: ratesSeconds)
        guard cycleTiming != timing else { return }
        cycleTiming = timing
        defaults.set(timing.numbersSeconds, forKey: "numbersDisplaySeconds")
        defaults.set(timing.ratesSeconds, forKey: "ratesDisplaySeconds")
        if cycleCancellable != nil { startDisplayCycle() }
    }

    private func startDisplayCycle() {
        cycleCancellable?.cancel()
        cycleStartedAt = ProcessInfo.processInfo.systemUptime
        showingUsageRates = false
        cycleCancellable = Timer.publish(every: 0.2, on: .main, in: .common)
            .autoconnect()
            .sink { [weak self] _ in
                guard let self else { return }
                self.updateDisplayCycle(after: ProcessInfo.processInfo.systemUptime - self.cycleStartedAt)
            }
    }

    func updateDisplayCycle(after elapsed: TimeInterval) {
        let showRates = cycleTiming.showsRates(after: elapsed)
        if showingUsageRates != showRates { showingUsageRates = showRates }
    }

    var creditsTrend: TrendSeries? {
        guard let bucket = snapshot?.buckets.first else { return nil }
        return trend(for: .credits(bucketID: bucket.id))
    }

    func trend(for metric: TrendMetric) -> TrendSeries? {
        guard trendLinesEnabled else { return nil }
        return TrendSeries.make(samples: history, metric: metric, range: trendTimeRange, now: Date())
    }

    var trendHistoryDuration: String {
        let now = Date()
        let visible = history.filter { $0.timestamp >= now.addingTimeInterval(-trendTimeRange.duration) }
        guard visible.count >= 2, let first = visible.first else { return "Collecting" }
        let minutes = max(0, Int(now.timeIntervalSince(first.timestamp) / 60))
        if minutes < 1 { return "<1m" }
        if minutes < 60 { return "\(minutes)m" }
        let hours = minutes / 60
        if hours < 24 { return "\(hours)h \(minutes % 60)m" }
        return "\(hours / 24)d \(hours % 24)h"
    }

    var trendHistorySummary: String {
        trendHistoryDuration == "Collecting"
            ? "Collecting trend history while Codex Meter is running."
            : "\(trendHistoryDuration) of recorded history · \(trendTimeRange.rawValue) selected"
    }

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
        cycleCancellable?.cancel()
        cycleCancellable = nil
        showingUsageRates = false
        client.stop()
        historyStorage.flush()
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

    func apply(_ snapshot: RateLimitResponse, at date: Date = Date()) {
        self.snapshot = snapshot
        history = historyStorage.record(snapshot, at: date)
        historySaveError = historyStorage.persistenceError?.localizedDescription
        state = .live
        lastUpdated = date
        errorMessage = nil
        isRefreshing = false
        if cycleCancellable == nil { startDisplayCycle() }
    }

    private func apply(_ error: Error) {
        errorMessage = error.localizedDescription
        state = .disconnected
        isRefreshing = false
    }
}
