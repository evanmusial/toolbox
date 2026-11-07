import XCTest
@testable import CodexMeter

final class UsageRateTests: XCTestCase {
    private let now = Date(timeIntervalSince1970: 1_800_000_000)

    func testAverageWeightsOnlyConsumingIntervalsByTheirActualDuration() throws {
        let metric = TrendMetric.quota(windowID: "codex-primary")
        let samples = readings(metric, offsets: [-300, -240, -180, -120, 0], values: [100, 98, 98, 100, 96])
        let rate = try XCTUnwrap(ActiveUsageRate.make(samples: samples, metric: metric, range: .oneHour, now: now))

        XCTAssertEqual(rate.consumed, 6)
        XCTAssertEqual(rate.activeSeconds, 180)
        XCTAssertEqual(rate.perHour, 120)
    }

    func testMissingReadingsAndLongOfflineGapsDoNotBecomeActiveTime() throws {
        let metric = TrendMetric.credits(bucketID: "codex")
        var samples = readings(metric, offsets: [-1000, -940, -820, -760, 0], values: [100, 95, 80, 75, 70])
        samples.insert(UsageHistorySample(timestamp: now.addingTimeInterval(-880), values: [:]), at: 2)
        let rate = try XCTUnwrap(ActiveUsageRate.make(samples: samples, metric: metric, range: .oneHour, now: now))

        XCTAssertEqual(rate.consumed, 10)
        XCTAssertEqual(rate.activeSeconds, 120)
        XCTAssertEqual(rate.perHour, 300)
    }

    func testNoConsumptionDoesNotProduceAFakeRate() {
        let metric = TrendMetric.credits(bucketID: "codex")
        for values in [[100.0, 100], [100.0, 200]] {
            let samples = readings(metric, offsets: [-60, 0], values: values)
            XCTAssertNil(ActiveUsageRate.make(samples: samples, metric: metric, range: .oneHour, now: now))
        }
        XCTAssertNil(ActiveUsageRate.make(samples: [], metric: metric, range: .oneHour, now: now))
    }

    func testRangeFilteringAndMetricKeysKeepAveragesIndependent() throws {
        let credits = TrendMetric.credits(bucketID: "codex")
        let quota = TrendMetric.quota(windowID: "codex-primary")
        let samples = readings(credits, offsets: [-7200, -120, -60, 1], values: [1000, 800, 790, 100])
        let rate = try XCTUnwrap(ActiveUsageRate.make(samples: samples, metric: credits, range: .oneHour, now: now))

        XCTAssertEqual(rate.perHour, 600)
        XCTAssertNil(ActiveUsageRate.make(samples: samples, metric: quota, range: .oneHour, now: now))
    }

    func testDuplicateTimestampsCannotDivideByZero() {
        let metric = TrendMetric.credits(bucketID: "codex")
        let samples = readings(metric, offsets: [0, 0], values: [100, 90])
        XCTAssertNil(ActiveUsageRate.make(samples: samples, metric: metric, range: .oneHour, now: now))
    }

    func testDefaultAndCustomCycleBoundariesRepeatWithoutExtraPhases() {
        let defaults = UsageCycleTiming()
        XCTAssertFalse(defaults.showsRates(after: 0))
        XCTAssertFalse(defaults.showsRates(after: 14.99))
        XCTAssertTrue(defaults.showsRates(after: 15))
        XCTAssertTrue(defaults.showsRates(after: 19.99))
        XCTAssertFalse(defaults.showsRates(after: 20))
        XCTAssertTrue(defaults.showsRates(after: 35))

        let custom = UsageCycleTiming(numbersSeconds: 7, ratesSeconds: 3)
        XCTAssertFalse(custom.showsRates(after: 6.99))
        XCTAssertTrue(custom.showsRates(after: 7))
        XCTAssertFalse(custom.showsRates(after: 10))
        XCTAssertTrue(custom.showsRates(after: 17))
        XCTAssertFalse(custom.showsRates(after: .infinity))
    }

    @MainActor
    func testTimingPreferencesSurviveRestartAndChangesRestartTheNumbersPhase() throws {
        let suite = "CodexMeterRateTests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: folder) }
        let url = folder.appendingPathComponent("usage-history.json")
        let store = QuotaStore(defaults: defaults, historyStorage: UsageHistoryStorage(url: url))

        XCTAssertEqual(store.cycleTiming, UsageCycleTiming(numbersSeconds: 15, ratesSeconds: 5))
        let snapshot = try JSONDecoder().decode(RateLimitResponse.self, from: Data(#"{"rateLimits":{"primary":{"usedPercent":25}}}"#.utf8))
        store.apply(snapshot)
        store.updateDisplayCycle(after: 15)
        XCTAssertTrue(store.showingUsageRates)
        store.setCycleTiming(numbersSeconds: 7, ratesSeconds: 3)
        XCTAssertFalse(store.showingUsageRates)
        let restored = QuotaStore(defaults: defaults, historyStorage: UsageHistoryStorage(url: url))
        XCTAssertEqual(restored.cycleTiming, UsageCycleTiming(numbersSeconds: 7, ratesSeconds: 3))
        XCTAssertFalse(restored.showingUsageRates)
        XCTAssertEqual(UsageCycleTiming(numbersSeconds: 0, ratesSeconds: -5), UsageCycleTiming(numbersSeconds: 1, ratesSeconds: 1))
    }

    private func readings(_ metric: TrendMetric, offsets: [TimeInterval], values: [Double]) -> [UsageHistorySample] {
        zip(offsets, values).map {
            UsageHistorySample(timestamp: now.addingTimeInterval($0.0), values: [metric.key: $0.1])
        }
    }
}
