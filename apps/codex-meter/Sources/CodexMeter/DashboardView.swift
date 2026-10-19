import SwiftUI

enum CodexPalette {
    // Codex dark mode with a restrained navy cast.
    static let background = Color(red: 0.102, green: 0.114, blue: 0.145) // #1A1D25
    static let surface = Color(red: 0.145, green: 0.161, blue: 0.204) // #252934
    static let surfaceRaised = Color(red: 0.184, green: 0.204, blue: 0.255) // #2F3441
    static let border = Color(red: 0.47, green: 0.55, blue: 0.78).opacity(0.16)
    static let text = Color(red: 0.949, green: 0.949, blue: 0.949) // #F2F2F2
    static let secondaryText = Color.white.opacity(0.70)
    static let accent = Color(red: 0.22, green: 0.35, blue: 1.00)
    static let accentLight = Color(red: 0.66, green: 0.62, blue: 1.00)
    static let warning = Color(red: 0.52, green: 0.45, blue: 1.00)
    static let danger = Color(red: 1.00, green: 0.42, blue: 0.48)
}

struct DashboardView: View {
    @ObservedObject var store: QuotaStore

    var body: some View {
        ZStack {
            CodexPalette.background.ignoresSafeArea()

            VStack(spacing: 0) {
                header
                Divider().overlay(CodexPalette.border)

                ScrollView {
                    if let snapshot = store.snapshot {
                        metricBoard(snapshot)
                    } else {
                        unavailableState
                    }
                }
                .scrollIndicators(.hidden)
            }
        }
        .foregroundStyle(CodexPalette.text)
        .preferredColorScheme(.dark)
        .frame(minWidth: 720, minHeight: 340)
    }

    private var header: some View {
        HStack(spacing: 12) {
            ZStack {
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .fill(CodexPalette.surfaceRaised)
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .stroke(CodexPalette.border, lineWidth: 1)
                Image(systemName: "terminal.fill")
                    .font(.system(size: 18, weight: .semibold))
                    .foregroundStyle(
                        LinearGradient(
                            colors: [CodexPalette.accentLight, CodexPalette.accent],
                            startPoint: .topLeading,
                            endPoint: .bottomTrailing
                        )
                    )
            }
            .frame(width: 42, height: 42)

            Text("Codex Meter")
                .font(.system(size: 20, weight: .semibold))

            Spacer()

            if let lastUpdated = store.lastUpdated {
                VStack(alignment: .trailing, spacing: 2) {
                    Text(lastUpdated.formatted(date: .omitted, time: .standard))
                        .font(.system(size: 14, weight: .semibold, design: .rounded))
                        .monospacedDigit()
                    Text("Updated")
                        .font(.system(size: 12))
                        .foregroundStyle(CodexPalette.secondaryText)
                }
            }

            HStack(spacing: 7) {
                Circle()
                    .fill(statusColor)
                    .frame(width: 8, height: 8)
                    .shadow(color: statusColor.opacity(0.6), radius: 4)
                Text(store.state.title)
                    .font(.system(size: 13, weight: .semibold))
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .background(CodexPalette.surface)
            .clipShape(Capsule())
            .overlay(Capsule().stroke(CodexPalette.border, lineWidth: 1))

            Button(action: store.refresh) {
                Image(systemName: "arrow.clockwise")
                    .font(.system(size: 15, weight: .semibold))
                    .frame(width: 34, height: 34)
            }
            .buttonStyle(QuietButtonStyle())
            .help("Refresh")
            .accessibilityLabel("Refresh Codex usage")
        }
        .padding(.horizontal, 24)
        .padding(.vertical, 12)
    }

    private var statusColor: Color {
        switch store.state {
        case .live: return CodexPalette.accent
        case .connecting: return CodexPalette.warning
        case .disconnected: return CodexPalette.danger
        }
    }

    private func metricBoard(_ snapshot: RateLimitResponse) -> some View {
        VStack(spacing: 18) {
            ForEach(snapshot.buckets) { bucket in
                BucketMetricsView(bucket: bucket)
            }
            AccountMetricsView(snapshot: snapshot)
        }
        .padding(.horizontal, 24)
        .padding(.vertical, 18)
    }

    private var unavailableState: some View {
        VStack(spacing: 18) {
            Spacer(minLength: 100)
            Text(store.errorMessage == nil ? "—" : "Unavailable")
                .font(.system(size: 42, weight: .semibold, design: .rounded))
            Text(store.errorMessage == nil ? "Codex usage" : "Codex app-server")
                .font(.system(size: 16, weight: .medium))
                .foregroundStyle(CodexPalette.secondaryText)
            if store.errorMessage != nil {
                Button("Retry", action: store.connect)
                    .buttonStyle(AccentButtonStyle())
            } else {
                ProgressView().controlSize(.small).tint(CodexPalette.accent)
            }
            Spacer(minLength: 100)
        }
        .frame(maxWidth: .infinity)
    }
}

private struct BucketMetricsView: View {
    let bucket: RateLimitSnapshot

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                Text(bucket.displayName)
                    .font(.system(size: 18, weight: .semibold))
                Spacer()
                if let plan = bucket.planType {
                    Text(plan.replacingOccurrences(of: "_", with: " ").capitalized)
                        .font(.system(size: 14, weight: .medium))
                        .foregroundStyle(CodexPalette.secondaryText)
                }
            }

            ForEach(bucket.windows) { item in
                WindowMetricsCard(item: item)
            }
        }
    }
}

private struct WindowMetricsCard: View {
    let item: NamedRateLimitWindow

    var body: some View {
        HStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 5) {
                Text(item.name)
                    .font(.system(size: 16, weight: .semibold))
                Text(windowLength)
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(CodexPalette.secondaryText)
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            MetricValue(value: "\(item.window.remainingPercent)%", label: "Remaining", tint: remainingColor, valueSize: 40)
            MetricDivider()
            MetricValue(value: "\(item.window.usedPercent)%", label: "Used", valueSize: 30)
            MetricDivider()
            MetricValue(value: resetValue, label: "Resets", valueSize: 20)
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 14)
        .background(CodexPalette.surface)
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .stroke(CodexPalette.border, lineWidth: 1)
        )
    }

    private var windowLength: String {
        guard let minutes = item.window.windowDurationMins else { return "Metered" }
        if minutes % 10_080 == 0 { return "7 days" }
        if minutes % 1_440 == 0 { return "\(minutes / 1_440) days" }
        if minutes % 60 == 0 { return "\(minutes / 60) hours" }
        return "\(minutes) minutes"
    }

    private var resetValue: String {
        guard let date = item.window.resetDate else { return "—" }
        return date.formatted(.dateTime.month(.abbreviated).day().hour().minute())
    }

    private var remainingColor: Color {
        switch item.window.remainingPercent {
        case 40...: return CodexPalette.accent
        case 15..<40: return CodexPalette.warning
        default: return CodexPalette.danger
        }
    }
}

private struct AccountMetricsView: View {
    let snapshot: RateLimitResponse
    private let columns = [
        GridItem(.flexible(), spacing: 12),
        GridItem(.flexible(), spacing: 12),
        GridItem(.flexible(), spacing: 12)
    ]

    var body: some View {
        LazyVGrid(columns: columns, spacing: 12) {
            MetricCard(
                value: accessStatus.value,
                label: accessStatus.label,
                tint: accessStatus.tint
            )
            MetricCard(value: resetCreditsValue, label: "Reset credits")
            MetricCard(value: workspaceCreditsValue, label: "Workspace credits", tint: CodexPalette.accent)
        }
    }

    private var primaryBucket: RateLimitSnapshot? { snapshot.buckets.first }

    private var accessStatus: (value: String, label: String, tint: Color) {
        switch primaryBucket?.rateLimitReachedType {
        case "rate_limit_reached":
            return ("Reached", "Rate limit", CodexPalette.danger)
        case "workspace_owner_credits_depleted", "workspace_member_credits_depleted":
            return ("Depleted", "Workspace credits", CodexPalette.danger)
        case "workspace_owner_usage_limit_reached", "workspace_member_usage_limit_reached":
            return ("Reached", "Usage limit", CodexPalette.danger)
        default:
            switch snapshot.ordinaryUsageAllowed {
            case true: return ("Available", "Plan usage", CodexPalette.accentLight)
            case false: return ("Unavailable", "Plan usage", CodexPalette.danger)
            case nil: return ("—", "Plan usage", CodexPalette.text)
            }
        }
    }

    private var resetCreditsValue: String {
        guard let count = snapshot.rateLimitResetCredits?.availableCount else { return "—" }
        return "\(count)"
    }

    private var workspaceCreditsValue: String {
        guard let credits = primaryBucket?.credits else { return "—" }
        if credits.unlimited { return "Unlimited" }
        if let balance = credits.formattedBalance { return balance }
        return credits.hasCredits ? "Available" : "0"
    }
}

private struct MetricCard: View {
    let value: String
    let label: String
    var tint: Color = CodexPalette.text

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(value)
                .font(.system(size: 28, weight: .semibold, design: .rounded))
                .foregroundStyle(tint)
                .monospacedDigit()
                .lineLimit(1)
                .minimumScaleFactor(0.65)
            Text(label)
                .font(.system(size: 14, weight: .medium))
                .foregroundStyle(CodexPalette.secondaryText)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 18)
        .padding(.vertical, 14)
        .background(CodexPalette.surface)
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .stroke(CodexPalette.border, lineWidth: 1)
        )
    }
}

private struct MetricValue: View {
    let value: String
    let label: String
    var tint: Color = CodexPalette.text
    let valueSize: CGFloat

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(value)
                .font(.system(size: valueSize, weight: .semibold, design: .rounded))
                .foregroundStyle(tint)
                .monospacedDigit()
                .lineLimit(1)
                .minimumScaleFactor(0.7)
            Text(label)
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(CodexPalette.secondaryText)
        }
        .frame(width: 150, alignment: .leading)
        .padding(.horizontal, 18)
    }
}

private struct MetricDivider: View {
    var body: some View {
        Rectangle()
            .fill(CodexPalette.border)
            .frame(width: 1, height: 54)
    }
}

private struct QuietButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .foregroundStyle(configuration.isPressed ? CodexPalette.secondaryText : CodexPalette.text)
            .background(configuration.isPressed ? CodexPalette.surfaceRaised : CodexPalette.surface)
            .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .stroke(CodexPalette.border, lineWidth: 1)
            )
    }
}

private struct AccentButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 14, weight: .semibold))
            .foregroundStyle(Color.black.opacity(0.82))
            .padding(.horizontal, 16)
            .padding(.vertical, 9)
            .background(CodexPalette.accent.opacity(configuration.isPressed ? 0.75 : 1))
            .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
    }
}
