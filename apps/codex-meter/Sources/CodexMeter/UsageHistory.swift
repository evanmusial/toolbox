import Foundation

enum TrendTimeRange: String, CaseIterable, Identifiable {
    case sevenDays = "7d"
    case threeDays = "3d"
    case oneDay = "24h"
    case twelveHours = "12h"
    case sixHours = "6h"
    case threeHours = "3h"
    case oneHour = "1h"

    var id: String { rawValue }

    var duration: TimeInterval {
        switch self {
        case .sevenDays: return 7 * 24 * 60 * 60
        case .threeDays: return 3 * 24 * 60 * 60
        case .oneDay: return 24 * 60 * 60
        case .twelveHours: return 12 * 60 * 60
        case .sixHours: return 6 * 60 * 60
        case .threeHours: return 3 * 60 * 60
        case .oneHour: return 60 * 60
        }
    }
}

enum TrendMetric: Hashable {
    case quota(windowID: String)
    case credits(bucketID: String)
    case resetCredits

    var key: String {
        switch self {
        case .quota(let id): return "quota:\(id)"
        case .credits(let id): return "credits:\(id)"
        case .resetCredits: return "resetCredits"
        }
    }

}

struct UsageHistorySample: Codable, Equatable {
    let timestamp: Date
    let values: [String: Double]

    init(timestamp: Date, values: [String: Double]) {
        self.timestamp = timestamp
        self.values = values.filter { $0.value.isFinite }
    }

    init(snapshot: RateLimitResponse, timestamp: Date) {
        var values: [String: Double] = [:]
        for bucket in snapshot.buckets {
            for item in bucket.windows {
                values[TrendMetric.quota(windowID: item.id).key] = Double(item.window.remainingPercent)
            }
            if let credits = bucket.credits, !credits.unlimited,
               let balance = credits.balance.flatMap(Double.init), balance.isFinite {
                values[TrendMetric.credits(bucketID: bucket.id).key] = balance
            }
        }
        if let resets = snapshot.rateLimitResetCredits?.availableCount, resets >= 0 {
            values[TrendMetric.resetCredits.key] = Double(resets)
        }
        self.init(timestamp: timestamp, values: values)
    }
}

struct UsageHistoryArchive: Codable {
    var version = 1
    var accounts: [String: [UsageHistorySample]] = [:]
}

/// One observed reading per minute, retained locally for seven days per account.
final class UsageHistoryStorage {
    static let retention: TimeInterval = 7 * 24 * 60 * 60
    static var defaultURL: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Codex Meter", isDirectory: true)
            .appendingPathComponent("usage-history.json")
    }

    private let url: URL
    private var archive: UsageHistoryArchive
    private var lastSaveAttempt: Date?
    private(set) var persistenceError: Error?

    init(url: URL = UsageHistoryStorage.defaultURL) {
        self.url = url
        if let data = try? Data(contentsOf: url),
           let saved = try? JSONDecoder().decode(UsageHistoryArchive.self, from: data),
           saved.version == 1 {
            archive = saved
        } else {
            archive = UsageHistoryArchive()
        }
    }

    @discardableResult
    func record(_ snapshot: RateLimitResponse, at date: Date) -> [UsageHistorySample] {
        let cutoff = date.addingTimeInterval(-Self.retention)
        for key in Array(archive.accounts.keys) {
            archive.accounts[key] = archive.accounts[key]?.filter {
                $0.timestamp >= cutoff && $0.timestamp <= date
            }
            if archive.accounts[key]?.isEmpty == true { archive.accounts.removeValue(forKey: key) }
        }

        let account = snapshot.accountId.map { "account:\($0)" } ?? "unidentified-account"
        let reading = UsageHistorySample(snapshot: snapshot, timestamp: date)
        var samples = archive.accounts[account] ?? []
        let minute = floor(date.timeIntervalSince1970 / 60)
        if let last = samples.last,
           floor(last.timestamp.timeIntervalSince1970 / 60) == minute {
            samples[samples.count - 1] = reading
        } else {
            samples.append(reading)
        }
        archive.accounts[account] = samples

        // Keep the live point fresh at the five-second poll cadence, without
        // rewriting the seven-day archive on every poll.
        if lastSaveAttempt.map({ date.timeIntervalSince($0) >= 60 }) ?? true {
            save(at: date)
        }
        return samples
    }

    func flush() {
        save(at: Date())
    }

    private func save(at date: Date) {
        lastSaveAttempt = date
        do {
            try FileManager.default.createDirectory(
                at: url.deletingLastPathComponent(), withIntermediateDirectories: true
            )
            let data = try JSONEncoder().encode(archive)
            try data.write(to: url, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
            persistenceError = nil
        } catch {
            persistenceError = error
        }
    }
}

struct TrendPoint: Equatable {
    /// Position across the available observations within the selected lookback.
    let x: Double
    /// Fraction of the vertical scale, increasing upward.
    let y: Double
}

struct TrendSeries: Equatable {
    let segments: [[TrendPoint]]

    static func make(
        samples: [UsageHistorySample], metric: TrendMetric,
        range: TrendTimeRange, now: Date
    ) -> TrendSeries? {
        let start = now.addingTimeInterval(-range.duration)
        let visible = samples.filter { $0.timestamp >= start && $0.timestamp <= now }
            .sorted { $0.timestamp < $1.timestamp }
        let values = visible.compactMap { $0.values[metric.key] }.filter(\.isFinite)
        guard let minimum = values.min(), let maximum = values.max() else { return nil }
        guard let firstObservation = visible.first(where: { $0.values[metric.key]?.isFinite == true }) else {
            return nil
        }
        let observedDuration = max(1, now.timeIntervalSince(firstObservation.timestamp))

        let lower: Double
        let upper: Double
        let constantPosition: Double
        switch metric {
        case .quota:
            lower = 0
            upper = 100
            constantPosition = 0
        case .credits:
            lower = minimum
            upper = maximum
            constantPosition = 0.5
        case .resetCredits:
            lower = 0
            upper = maximum
            constantPosition = 0
        }
        let span = upper - lower

        var segments: [[TrendPoint]] = []
        var current: [TrendPoint] = []
        var previousDate: Date?
        for sample in visible {
            guard let value = sample.values[metric.key], value.isFinite else {
                if !current.isEmpty { segments.append(current) }
                current = []
                previousDate = nil
                continue
            }
            // Do not imply continuous observations across sleep or an outage.
            if let previousDate, sample.timestamp.timeIntervalSince(previousDate) > 5 * 60 {
                if !current.isEmpty { segments.append(current) }
                current = []
            }
            let x = values.count == 1 ? 1 : sample.timestamp.timeIntervalSince(firstObservation.timestamp) / observedDuration
            let y = span > 0 ? min(1, max(0, (value - lower) / span)) : constantPosition
            current.append(TrendPoint(x: x, y: y))
            previousDate = sample.timestamp
        }
        if !current.isEmpty { segments.append(current) }
        return TrendSeries(segments: segments)
    }
}
