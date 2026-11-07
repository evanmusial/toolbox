import XCTest
@testable import CodexMeter

final class UsageHistoryTests: XCTestCase {
    private let start = Date(timeIntervalSince1970: 1_800_000_000)

    func testRecordsEachQuotaWindowAndOnlyFiniteNumericCreditBalances() {
        let snapshot = response(remaining: 75, balance: "10566.8265")
        let sample = UsageHistorySample(snapshot: snapshot, timestamp: start)

        XCTAssertEqual(sample.values[TrendMetric.quota(windowID: "codex-primary").key], 75)
        XCTAssertEqual(sample.values[TrendMetric.quota(windowID: "codex-secondary").key], 60)
        XCTAssertEqual(sample.values[TrendMetric.credits(bucketID: "codex").key], 10566.8265)
        XCTAssertEqual(sample.values[TrendMetric.resetCredits.key], 2)

        for balance in [nil, "Available", "NaN", "inf"] as [String?] {
            let unknown = UsageHistorySample(snapshot: response(balance: balance), timestamp: start)
            XCTAssertNil(unknown.values[TrendMetric.credits(bucketID: "codex").key])
        }
        let unlimited = UsageHistorySample(snapshot: response(unlimited: true), timestamp: start)
        XCTAssertNil(unlimited.values[TrendMetric.credits(bucketID: "codex").key])
    }

    func testCoalescesPollsWithinAMinuteAndExpiresOldReadings() throws {
        let url = temporaryURL()
        defer { try? FileManager.default.removeItem(at: url.deletingLastPathComponent()) }
        let storage = UsageHistoryStorage(url: url)

        storage.record(response(remaining: 90), at: start)
        let current = storage.record(response(remaining: 80), at: start.addingTimeInterval(5))
        XCTAssertEqual(current.count, 1)
        XCTAssertEqual(current[0].timestamp, start.addingTimeInterval(5))
        XCTAssertEqual(current[0].values[TrendMetric.quota(windowID: "codex-primary").key], 80)

        XCTAssertEqual(storage.record(response(), at: start.addingTimeInterval(60)).count, 2)
        let expired = storage.record(response(), at: start.addingTimeInterval(UsageHistoryStorage.retention + 61))
        XCTAssertEqual(expired.count, 1)
        XCTAssertNil(storage.persistenceError)
    }

    func testAccountHistoriesStaySeparateIncludingAnUnidentifiedAccount() {
        let url = temporaryURL()
        defer { try? FileManager.default.removeItem(at: url.deletingLastPathComponent()) }
        let storage = UsageHistoryStorage(url: url)
        storage.record(response(account: "a", balance: "400"), at: start)
        storage.record(response(account: "b", balance: "900"), at: start.addingTimeInterval(1))
        storage.record(response(account: nil, balance: "1500"), at: start.addingTimeInterval(2))

        let a = storage.record(response(account: "a", balance: "300"), at: start.addingTimeInterval(61))
        let b = storage.record(response(account: "b", balance: "800"), at: start.addingTimeInterval(62))
        let unidentified = storage.record(response(account: nil), at: start.addingTimeInterval(63))
        let key = TrendMetric.credits(bucketID: "codex").key
        XCTAssertEqual(a.map { $0.values[key] }, [400, 300])
        XCTAssertEqual(b.map { $0.values[key] }, [900, 800])
        XCTAssertEqual(unidentified.first?.values[key], 1500)
    }

    func testFlushPersistsTheLatestLivePointAcrossARestart() throws {
        let url = temporaryURL()
        defer { try? FileManager.default.removeItem(at: url.deletingLastPathComponent()) }
        let storage = UsageHistoryStorage(url: url)
        storage.record(response(remaining: 90), at: start)
        storage.record(response(remaining: 35), at: start.addingTimeInterval(5))
        storage.flush()

        let reloaded = UsageHistoryStorage(url: url)
        let samples = reloaded.record(response(remaining: 30), at: start.addingTimeInterval(61))
        XCTAssertEqual(samples.count, 2)
        XCTAssertEqual(samples[0].values[TrendMetric.quota(windowID: "codex-primary").key], 35)
        XCTAssertEqual(samples[0].timestamp, start.addingTimeInterval(5))
        let permissions = try FileManager.default.attributesOfItem(atPath: url.path)[.posixPermissions] as? NSNumber
        XCTAssertEqual(permissions?.intValue, 0o600)
    }

    func testTrendFiltersBySelectedLookbackWithoutInventingReadings() throws {
        let metric = TrendMetric.quota(windowID: "codex-primary")
        let samples = [
            UsageHistorySample(timestamp: start.addingTimeInterval(-3601), values: [metric.key: 95]),
            UsageHistorySample(timestamp: start.addingTimeInterval(-1800), values: [metric.key: 60]),
            UsageHistorySample(timestamp: start.addingTimeInterval(1), values: [metric.key: 20])
        ]
        let series = try XCTUnwrap(TrendSeries.make(samples: samples, metric: metric, range: .oneHour, now: start))

        XCTAssertEqual(series.segments, [[TrendPoint(x: 1, y: 0.6)]])
        XCTAssertNil(TrendSeries.make(samples: [], metric: metric, range: .sevenDays, now: start))
    }

    func testAvailableHistoryFillsTheBandEvenBeforeTheSelectedRangeHasElapsed() throws {
        let metric = TrendMetric.quota(windowID: "codex-primary")
        let samples = [
            UsageHistorySample(timestamp: start.addingTimeInterval(-120), values: [metric.key: 80]),
            UsageHistorySample(timestamp: start, values: [metric.key: 75])
        ]
        let series = try XCTUnwrap(TrendSeries.make(samples: samples, metric: metric, range: .sevenDays, now: start))
        XCTAssertEqual(series.segments, [[TrendPoint(x: 0, y: 0.8), TrendPoint(x: 1, y: 0.75)]])
    }

    func testMissingMetricsAndOfflineGapsBreakTheLine() throws {
        let metric = TrendMetric.credits(bucketID: "codex")
        let samples = [
            UsageHistorySample(timestamp: start.addingTimeInterval(-600), values: [metric.key: 50]),
            UsageHistorySample(timestamp: start.addingTimeInterval(-540), values: [:]),
            UsageHistorySample(timestamp: start.addingTimeInterval(-480), values: [metric.key: 45]),
            UsageHistorySample(timestamp: start.addingTimeInterval(-60), values: [metric.key: 40])
        ]
        let series = try XCTUnwrap(TrendSeries.make(samples: samples, metric: metric, range: .oneHour, now: start))
        XCTAssertEqual(series.segments.map(\.count), [1, 1, 1])
        XCTAssertGreaterThan(series.segments[0][0].y, series.segments[2][0].y)
    }

    func testConstantCreditsStayCenteredAndQuotaAlwaysUsesZeroToOneHundred() throws {
        let credits = TrendMetric.credits(bucketID: "codex")
        let quota = TrendMetric.quota(windowID: "codex-primary")
        let samples = [
            UsageHistorySample(timestamp: start.addingTimeInterval(-60), values: [credits.key: 6500, quota.key: 40]),
            UsageHistorySample(timestamp: start, values: [credits.key: 6500, quota.key: 60])
        ]
        let flat = try XCTUnwrap(TrendSeries.make(samples: samples, metric: credits, range: .oneHour, now: start))
        XCTAssertEqual(flat.segments[0].map(\.y), [0.5, 0.5])
        let percentage = try XCTUnwrap(TrendSeries.make(samples: samples, metric: quota, range: .oneHour, now: start))
        XCTAssertEqual(percentage.segments[0].map(\.y), [0.4, 0.6])
    }

    func testCreditsUseTheSelectedRangesExactExtremaToRevealSmallChanges() throws {
        let metric = TrendMetric.credits(bucketID: "codex")
        let samples = [
            UsageHistorySample(timestamp: start.addingTimeInterval(-7200), values: [metric.key: 12000]),
            UsageHistorySample(timestamp: start.addingTimeInterval(-60), values: [metric.key: 6600]),
            UsageHistorySample(timestamp: start, values: [metric.key: 6500])
        ]
        let hour = try XCTUnwrap(TrendSeries.make(samples: samples, metric: metric, range: .oneHour, now: start))
        XCTAssertEqual(hour.segments[0].map(\.y), [1, 0])
        let day = try XCTUnwrap(TrendSeries.make(samples: samples, metric: metric, range: .oneDay, now: start))
        XCTAssertEqual(day.segments.last?.first?.y, 100.0 / 5500)
        XCTAssertEqual(day.segments.last?.last?.y, 0)
    }

    @MainActor
    func testSettingsPersistAndOffHidesAllTrendsWithoutDiscardingHistory() throws {
        let suite = "CodexMeterTests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let url = temporaryURL()
        defer { try? FileManager.default.removeItem(at: url.deletingLastPathComponent()) }
        let store = QuotaStore(defaults: defaults, historyStorage: UsageHistoryStorage(url: url))
        let now = Date()
        XCTAssertTrue(store.trendLinesEnabled)
        XCTAssertEqual(store.trendTimeRange, .oneDay)
        store.apply(response(), at: now.addingTimeInterval(-120))
        store.apply(response(), at: now)
        XCTAssertNotNil(store.primaryQuotaTrend)
        XCTAssertNotNil(store.creditsTrend)

        store.trendLinesEnabled = false
        store.trendTimeRange = .sevenDays
        XCTAssertNil(store.primaryQuotaTrend)
        XCTAssertNil(store.creditsTrend)
        XCTAssertNil(store.trend(for: .resetCredits))
        XCTAssertEqual(store.history.count, 2)

        let restored = QuotaStore(defaults: defaults, historyStorage: UsageHistoryStorage(url: url))
        XCTAssertFalse(restored.trendLinesEnabled)
        XCTAssertEqual(restored.trendTimeRange, .sevenDays)
    }

    private func temporaryURL() -> URL {
        FileManager.default.temporaryDirectory
            .appendingPathComponent("codex-meter-tests-\(UUID().uuidString)", isDirectory: true)
            .appendingPathComponent("usage-history.json")
    }

    private func response(
        account: String? = "a", remaining: Int = 75, balance: String? = "100", unlimited: Bool = false
    ) -> RateLimitResponse {
        RateLimitResponse(
            accountId: account, ordinaryUsageAllowed: true,
            rateLimitResetCredits: ResetCreditsSummary(availableCount: 2, credits: nil),
            rateLimits: RateLimitSnapshot(
                limitId: "codex", limitName: nil, normalModelSlug: nil, planType: "pro",
                primary: RateLimitWindow(usedPercent: 100 - remaining, windowDurationMins: 300, resetsAt: nil),
                secondary: RateLimitWindow(usedPercent: 40, windowDurationMins: 10080, resetsAt: nil),
                credits: CreditsSnapshot(balance: balance, hasCredits: true, unlimited: unlimited),
                individualLimit: nil, spendControlReached: nil, rateLimitReachedType: nil
            ),
            rateLimitsByLimitId: nil
        )
    }
}
