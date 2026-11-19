import Combine
import SwiftUI

struct RadioSettingsView: View {
    @ObservedObject var store: RadioStore
    @State private var menuAccess = false

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Wavewing")
                        .font(.system(size: 25, weight: .semibold, design: .rounded))
                    Text("Now playing on the left side of the notch")
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Toggle("Show HUD", isOn: $store.hudVisible)
                    .toggleStyle(.switch)
            }

            HStack {
                Text("Change stations every")
                Stepper(value: $store.cycleSeconds, in: 2...30, step: 1) {
                    Text("\(Int(store.cycleSeconds)) seconds")
                        .monospacedDigit()
                }
                .frame(width: 155)
                Spacer()
            }

            HStack(alignment: .center, spacing: 12) {
                Text(menuAccess
                     ? "Menu awareness is on. Wavewing follows other apps’ menu items."
                     : "Allow Accessibility to fit around other apps’ menu items. Until then, the wings stay compact.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer()
                if !menuAccess {
                    Button("Allow Menu Awareness") {
                        menuAccess = MenuBarSpaceProbe.requestAccess()
                    }
                }
            }

            Divider()
            Text("Stations")
                .font(.headline)
            Text("Choose which stations appear. New-track animations are off until enabled for each station.")
                .font(.caption)
                .foregroundStyle(.secondary)
            List(RadioStation.all) { station in
                HStack(alignment: .top, spacing: 12) {
                    Toggle("", isOn: Binding(
                        get: { store.isEnabled(station) },
                        set: { store.setEnabled($0, for: station) }
                    ))
                    .labelsHidden()
                    .accessibilityLabel("Show \(station.name)")
                    VStack(alignment: .leading, spacing: 4) {
                        HStack {
                            Text(station.name)
                                .bold()
                            Spacer()
                            Toggle("Animate", isOn: Binding(
                                get: { store.animatesNewTracks(for: station) },
                                set: { store.setNewTrackAnimationEnabled($0, for: station) }
                            ))
                            .toggleStyle(.switch)
                            .controlSize(.small)
                            .disabled(!store.isEnabled(station))
                            .accessibilityLabel("Animate new tracks for \(station.name)")
                        }
                        Text(store.text(for: station))
                            .lineLimit(2)
                            .textSelection(.enabled)
                        if let error = store.errors[station.group] {
                            Text(error)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        } else if let updated = store.lastUpdated[station.id] {
                            Text("Checked \(updated.formatted(.relative(presentation: .named)))")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                    Spacer(minLength: 0)
                }
                .padding(.vertical, 4)
            }
            .listStyle(.inset)
        }
        .padding(22)
        .frame(minWidth: 430, minHeight: 440)
        .preferredColorScheme(.dark)
        .onAppear { menuAccess = MenuBarSpaceProbe.hasAccess }
        .onReceive(Timer.publish(every: 2, on: .main, in: .common).autoconnect()) { _ in
            menuAccess = MenuBarSpaceProbe.hasAccess
        }
    }
}
