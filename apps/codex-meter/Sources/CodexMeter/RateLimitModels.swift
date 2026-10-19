import Foundation

struct RateLimitResponse: Decodable, Equatable {
    let accountId: String?
    let ordinaryUsageAllowed: Bool?
    let rateLimitResetCredits: ResetCreditsSummary?
    let rateLimits: RateLimitSnapshot
    let rateLimitsByLimitId: [String: RateLimitSnapshot]?

    var buckets: [RateLimitSnapshot] {
        guard let keyed = rateLimitsByLimitId, !keyed.isEmpty else {
            return [rateLimits]
        }

        return keyed
            .map { key, value in value.withFallbackId(key) }
            .sorted { lhs, rhs in
                if lhs.limitId == "codex" { return true }
                if rhs.limitId == "codex" { return false }
                return lhs.displayName.localizedCaseInsensitiveCompare(rhs.displayName) == .orderedAscending
            }
    }

    var primaryWindow: RateLimitWindow? {
        buckets.first(where: { $0.limitId == "codex" })?.primary
            ?? buckets.compactMap(\.primary).first
    }
}

struct RateLimitSnapshot: Decodable, Equatable, Identifiable {
    let limitId: String?
    let limitName: String?
    let normalModelSlug: String?
    let planType: String?
    let primary: RateLimitWindow?
    let secondary: RateLimitWindow?
    let credits: CreditsSnapshot?
    let individualLimit: SpendControlLimitSnapshot?
    let spendControlReached: Bool?
    let rateLimitReachedType: String?

    var id: String {
        limitId ?? limitName ?? "codex"
    }

    var displayName: String {
        if let limitName, !limitName.isEmpty, limitName != limitId {
            return limitName.replacingOccurrences(of: "_", with: " ").capitalized
        }

        switch limitId {
        case "codex", nil:
            return "Codex"
        case let id?:
            return id.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    var windows: [NamedRateLimitWindow] {
        var result: [NamedRateLimitWindow] = []
        if let primary {
            result.append(.init(id: "\(id)-primary", name: primary.displayName, window: primary))
        }
        if let secondary {
            result.append(.init(id: "\(id)-secondary", name: secondary.displayName, window: secondary))
        }
        return result
    }

    func withFallbackId(_ fallback: String) -> RateLimitSnapshot {
        guard limitId == nil else { return self }
        return RateLimitSnapshot(
            limitId: fallback,
            limitName: limitName,
            normalModelSlug: normalModelSlug,
            planType: planType,
            primary: primary,
            secondary: secondary,
            credits: credits,
            individualLimit: individualLimit,
            spendControlReached: spendControlReached,
            rateLimitReachedType: rateLimitReachedType
        )
    }
}

struct NamedRateLimitWindow: Identifiable, Equatable {
    let id: String
    let name: String
    let window: RateLimitWindow
}

struct RateLimitWindow: Decodable, Equatable {
    let usedPercent: Int
    let windowDurationMins: Int?
    let resetsAt: Int?

    var remainingPercent: Int {
        min(100, max(0, 100 - usedPercent))
    }

    var displayName: String {
        guard let minutes = windowDurationMins else { return "Usage window" }
        if minutes % 10_080 == 0 { return "Weekly window" }
        if minutes % 1_440 == 0 {
            let days = minutes / 1_440
            return days == 1 ? "Daily window" : "\(days)-day window"
        }
        if minutes % 60 == 0 {
            let hours = minutes / 60
            return "\(hours)-hour window"
        }
        return "\(minutes)-minute window"
    }

    var resetDate: Date? {
        resetsAt.map { Date(timeIntervalSince1970: TimeInterval($0)) }
    }
}

struct CreditsSnapshot: Decodable, Equatable {
    let balance: String?
    let hasCredits: Bool
    let unlimited: Bool

    var formattedBalance: String? {
        guard let balance else { return nil }
        guard let decimal = Decimal(string: balance, locale: Locale(identifier: "en_US_POSIX")) else {
            return balance
        }

        let formatter = NumberFormatter()
        formatter.numberStyle = .decimal
        formatter.minimumFractionDigits = 2
        formatter.maximumFractionDigits = 2
        return formatter.string(from: decimal as NSDecimalNumber) ?? balance
    }
}

struct SpendControlLimitSnapshot: Decodable, Equatable {
    let limit: String
    let remainingPercent: Int
    let resetsAt: Int
    let used: String
}

struct ResetCreditsSummary: Decodable, Equatable {
    let availableCount: Int
    let credits: [ResetCredit]?
}

struct ResetCredit: Decodable, Equatable, Identifiable {
    let id: String
    let resetType: String
    let status: String
    let grantedAt: Int
    let expiresAt: Int?
    let title: String?
    let description: String?
}
