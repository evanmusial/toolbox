import SwiftUI

struct RotatingUsageReadout<Numbers: View, Rate: View>: View {
    let showRate: Bool
    var alignment: Alignment = .center
    var travel: CGFloat = 24
    let numbers: () -> Numbers
    let rate: () -> Rate
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    init(
        showRate: Bool, alignment: Alignment = .center, travel: CGFloat = 24,
        @ViewBuilder numbers: @escaping () -> Numbers, @ViewBuilder rate: @escaping () -> Rate
    ) {
        self.showRate = showRate
        self.alignment = alignment
        self.travel = travel
        self.numbers = numbers
        self.rate = rate
    }

    var body: some View {
        ZStack(alignment: alignment) {
            numbers()
                .offset(y: reduceMotion ? 0 : (showRate ? -travel : 0))
                .opacity(showRate ? 0 : 1)
                .accessibilityHidden(showRate)
            rate()
                .offset(y: reduceMotion ? 0 : (showRate ? 0 : travel))
                .opacity(showRate ? 1 : 0)
                .accessibilityHidden(!showRate)
        }
        .clipped()
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.3), value: showRate)
    }
}

struct CompactUsageRate: View {
    let rate: ActiveUsageRate
    let units: String

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 3) {
            Text(rate.formattedValue)
                .font(.system(size: 12, weight: .bold, design: .rounded))
                .monospacedDigit()
            Text(units)
                .font(.system(size: 11, weight: .regular, design: .rounded))
        }
        .lineLimit(1)
        .minimumScaleFactor(0.65)
    }
}

struct UsageTimingControls: View {
    @ObservedObject var store: QuotaStore

    var body: some View {
        HStack(spacing: 16) {
            Text("HUD rotation")
                .foregroundStyle(CodexPalette.secondaryText)
            durationField("Numbers", value: Binding(
                get: { store.cycleTiming.numbersSeconds },
                set: { store.setCycleTiming(numbersSeconds: $0, ratesSeconds: store.cycleTiming.ratesSeconds) }
            ))
            durationField("Rates", value: Binding(
                get: { store.cycleTiming.ratesSeconds },
                set: { store.setCycleTiming(numbersSeconds: store.cycleTiming.numbersSeconds, ratesSeconds: $0) }
            ))
            Spacer(minLength: 0)
        }
        .font(.system(size: 12))
        .help("Active-use averages exclude idle intervals, resets, credit additions, and gaps over five minutes. Timing accepts 1–3600 seconds.")
    }

    private func durationField(_ title: String, value: Binding<Int>) -> some View {
        HStack(spacing: 5) {
            Text(title)
            TextField(title, value: value, format: .number.grouping(.never))
                .textFieldStyle(.roundedBorder)
                .multilineTextAlignment(.trailing)
                .frame(width: 46)
                .accessibilityLabel("\(title) display duration in seconds")
                .accessibilityIdentifier("\(title.lowercased())-display-seconds")
            Text("s").foregroundStyle(CodexPalette.secondaryText)
        }
    }
}
