import XCTest
@testable import CodexMeter

final class RateLimitModelsTests: XCTestCase {
    func testDecodesCurrentAppServerShape() throws {
        let json = #"""
        {
          "accountId": "workspace-example",
          "ordinaryUsageAllowed": true,
          "rateLimits": {
            "limitId": "codex",
            "planType": "pro",
            "primary": { "usedPercent": 25, "windowDurationMins": 300, "resetsAt": 1893456000 },
            "secondary": { "usedPercent": 40, "windowDurationMins": 10080, "resetsAt": 1894060800 },
            "credits": { "balance": "12.50", "hasCredits": true, "unlimited": false }
          },
          "rateLimitsByLimitId": {
            "codex": {
              "limitId": "codex",
              "planType": "pro",
              "primary": { "usedPercent": 25, "windowDurationMins": 300, "resetsAt": 1893456000 },
              "secondary": { "usedPercent": 40, "windowDurationMins": 10080, "resetsAt": 1894060800 },
              "credits": { "balance": "12.50", "hasCredits": true, "unlimited": false }
            }
          },
          "rateLimitResetCredits": { "availableCount": 2, "credits": null }
        }
        """#

        let response = try JSONDecoder().decode(RateLimitResponse.self, from: Data(json.utf8))

        XCTAssertEqual(response.primaryWindow?.remainingPercent, 75)
        XCTAssertEqual(response.primaryWindow?.displayName, "5-hour window")
        XCTAssertEqual(response.buckets.first?.secondary?.displayName, "Weekly window")
        XCTAssertEqual(response.buckets.first?.credits?.balance, "12.50")
        XCTAssertEqual(response.buckets.first?.credits?.formattedBalance, "12.50")
        XCTAssertEqual(response.rateLimitResetCredits?.availableCount, 2)
    }

    func testFormatsHighPrecisionCreditBalanceForDisplay() throws {
        let credits = CreditsSnapshot(balance: "10566.8265005000", hasCredits: true, unlimited: false)

        XCTAssertEqual(credits.formattedBalance, "10,566.83")
    }

    func testFallsBackToLegacySingleBucket() throws {
        let json = #"""
        {
          "rateLimits": {
            "primary": { "usedPercent": 110, "windowDurationMins": 60, "resetsAt": null },
            "secondary": null
          },
          "rateLimitsByLimitId": null
        }
        """#

        let response = try JSONDecoder().decode(RateLimitResponse.self, from: Data(json.utf8))

        XCTAssertEqual(response.buckets.count, 1)
        XCTAssertEqual(response.primaryWindow?.remainingPercent, 0)
        XCTAssertEqual(response.primaryWindow?.displayName, "1-hour window")
    }
}
