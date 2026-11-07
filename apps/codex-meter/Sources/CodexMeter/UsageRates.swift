import Foundation

struct ActiveUsageRate: Equatable {
    let consumed: Double
    let activeSeconds: TimeInterval

    var perHour: Double { consumed * 3600 / activeSeconds }

    var formattedValue: String {
        let formatter = NumberFormatter()
        formatter.numberStyle = .decimal
        formatter.minimumFractionDigits = 0
        formatter.maximumFractionDigits = perHour < 1 ? 2 : 1
        return formatter.string(from: NSNumber(value: perHour)) ?? "—"
    }

    static func make(
        samples: [UsageHistorySample], metric: TrendMetric,
        range: TrendTimeRange, now: Date
    ) -> ActiveUsageRate? {
        let cutoff = now.addingTimeInterval(-range.duration)
        let visible = samples.filter { $0.timestamp >= cutoff && $0.timestamp <= now }
            .sorted { $0.timestamp < $1.timestamp }
        var consumed = 0.0
        var activeSeconds = 0.0
        for (previous, current) in zip(visible, visible.dropFirst()) {
            let elapsed = current.timestamp.timeIntervalSince(previous.timestamp)
            guard elapsed > 0, elapsed <= 5 * 60,
                  let before = previous.values[metric.key], before.isFinite,
                  let after = current.values[metric.key], after.isFinite,
                  before > after else { continue }
            consumed += before - after
            activeSeconds += elapsed
        }
        guard consumed.isFinite, consumed > 0, activeSeconds > 0 else { return nil }
        let rate = ActiveUsageRate(consumed: consumed, activeSeconds: activeSeconds)
        return rate.perHour.isFinite ? rate : nil
    }
}

struct UsageCycleTiming: Equatable {
    let numbersSeconds: Int
    let ratesSeconds: Int

    init(numbersSeconds: Int = 15, ratesSeconds: Int = 5) {
        self.numbersSeconds = min(3600, max(1, numbersSeconds))
        self.ratesSeconds = min(3600, max(1, ratesSeconds))
    }

    func showsRates(after elapsed: TimeInterval) -> Bool {
        guard elapsed.isFinite, elapsed >= 0 else { return false }
        let cycle = TimeInterval(numbersSeconds + ratesSeconds)
        return elapsed.truncatingRemainder(dividingBy: cycle) >= TimeInterval(numbersSeconds)
    }
}
