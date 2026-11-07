import SwiftUI

struct TrendBackdrop: View {
    let series: TrendSeries?
    var height: CGFloat = 28
    var bottomInset: CGFloat = 8

    var body: some View {
        Canvas { context, size in
            guard let series, size.width > 6, size.height > 2 else { return }
            let horizontalInset: CGFloat = 4
            let verticalInset = min(4, size.height * 0.2)
            let width = size.width - horizontalInset * 2
            let height = size.height - verticalInset * 2

            for segment in series.segments {
                let points = segment.map {
                    CGPoint(x: horizontalInset + $0.x * width, y: verticalInset + (1 - $0.y) * height)
                }
                guard let first = points.first, let last = points.last else { continue }
                if points.count == 1 {
                    context.fill(
                        Path(ellipseIn: CGRect(x: first.x - 1.8, y: first.y - 1.8, width: 3.6, height: 3.6)),
                        with: .color(CodexPalette.trendBlue.opacity(0.85))
                    )
                    continue
                }

                var line = Path()
                line.addLines(points)
                var area = line
                area.addLine(to: CGPoint(x: last.x, y: size.height))
                area.addLine(to: CGPoint(x: first.x, y: size.height))
                area.closeSubpath()
                context.fill(area, with: .linearGradient(
                    Gradient(colors: [CodexPalette.trendBlue.opacity(0.09), CodexPalette.trendBlue.opacity(0.01)]),
                    startPoint: .zero, endPoint: CGPoint(x: 0, y: size.height)
                ))
                context.stroke(line, with: .color(CodexPalette.trendBlue.opacity(0.10)), lineWidth: 4)
                context.stroke(
                    line, with: .color(CodexPalette.trendBlue.opacity(0.72)),
                    style: StrokeStyle(lineWidth: size.height < 10 ? 1.25 : 1.6, lineCap: .round, lineJoin: .round)
                )
            }
        }
        .frame(height: height)
        .padding(.bottom, bottomInset)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottom)
        .clipped()
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}

struct TrendControls: View {
    @ObservedObject var store: QuotaStore
    var compact = false

    var body: some View {
        HStack(spacing: compact ? 12 : 20) {
            Toggle(isOn: $store.trendLinesEnabled) {
                VStack(alignment: .leading, spacing: 1) {
                    Text(compact ? "Trends" : "Trend lines")
                    if compact {
                        Text(store.trendHistoryDuration == "Collecting" ? "Collecting" : "\(store.trendHistoryDuration) recorded")
                            .font(.system(size: 9))
                            .foregroundStyle(CodexPalette.secondaryText)
                    }
                }
                .meterTextShadow()
            }
            .toggleStyle(.switch)
            .fixedSize()
            .accessibilityIdentifier("trend-lines-toggle")

            Picker("Trend time range", selection: $store.trendTimeRange) {
                ForEach(TrendTimeRange.allCases) { range in
                    Text(range.rawValue).tag(range)
                }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .accessibilityLabel("Trend time range")
            .accessibilityIdentifier("trend-time-range")
            .help("Time range for trends and active-use averages. Oldest readings are on the left, newest on the right.")
        }
        .font(.system(size: compact ? 11 : 13, weight: .medium))
        .controlSize(compact ? .mini : .small)
        .tint(CodexPalette.trendBlue)
        .foregroundStyle(CodexPalette.text)
    }
}

extension View {
    func meterTextShadow() -> some View {
        shadow(color: .black, radius: 0.8, x: 0, y: 0)
            .shadow(color: .black, radius: 1.5, x: 0, y: 1)
    }
}
