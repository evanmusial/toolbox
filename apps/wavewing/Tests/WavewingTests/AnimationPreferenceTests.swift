import XCTest
@testable import Wavewing

@MainActor
final class AnimationPreferenceTests: XCTestCase {
    func testNewTrackAnimationIsOffByDefaultAndCanBeSelectedPerStation() {
        let suite = "WavewingTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        let store = RadioStore(defaults: defaults)
        let slam = RadioStation.all.first { $0.id == "slam" }!
        let bpm = RadioStation.all.first { $0.id == "bpm" }!

        XCTAssertFalse(store.animatesNewTracks(for: slam))
        XCTAssertFalse(store.animatesNewTracks(for: bpm))
        store.apply([track("A")], group: .slam)
        XCTAssertEqual(store.displayedID, slam.id)
        store.apply([track("Initial", stationID: bpm.id, eventAt: Date())], group: .siriusXM)
        store.apply([track("B")], group: .slam)
        store.apply([track("Changed", stationID: bpm.id, eventAt: Date())], group: .siriusXM)
        XCTAssertEqual(store.displayedID, slam.id, "An opted-out station must not jump into view")
        XCTAssertEqual(store.motion, .none)

        store.setNewTrackAnimationEnabled(true, for: slam)
        XCTAssertTrue(store.animatesNewTracks(for: slam))
        XCTAssertFalse(store.animatesNewTracks(for: bpm))
        XCTAssertTrue(RadioStore(defaults: defaults).animatesNewTracks(for: slam))

        store.apply([track("C")], group: .slam)
        XCTAssertEqual(store.displayedID, slam.id)
        XCTAssertEqual(store.motion, .glideWithTint)

        store.setNewTrackAnimationEnabled(false, for: slam)
        store.apply([track("D")], group: .slam)
        XCTAssertEqual(store.motion, .none, "The visible text should update without an animation")
        XCTAssertFalse(RadioStore(defaults: defaults).animatesNewTracks(for: slam))
    }

    func testPendingAndNonSongStationsAreSkipped() {
        let suite = "WavewingTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        let store = RadioStore(defaults: defaults)
        XCTAssertFalse(store.hasReadyMetadata)
        XCTAssertTrue(store.readyStations.isEmpty)
        XCTAssertNil(store.displayedID)

        store.apply([RadioTrack(stationID: "revolution997", artist: "", title: "On air",
                                identity: "onair", eventAt: nil, isSong: false)], group: .revolution997)
        XCTAssertFalse(store.hasReadyMetadata)

        store.apply([track("First", stationID: "bpm", eventAt: Date())], group: .siriusXM)
        XCTAssertTrue(store.hasReadyMetadata)
        XCTAssertEqual(store.readyStations.map(\.id), ["bpm"])
        XCTAssertEqual(store.displayedID, "bpm")

        store.apply([track("Second")], group: .slam)
        XCTAssertEqual(store.readyStations.map(\.id), ["slam", "bpm"])
        store.apply([RadioTrack(stationID: "bpm", artist: "", title: "On air",
                                identity: "bpm-onair", eventAt: nil, isSong: false)], group: .siriusXM)
        XCTAssertEqual(store.displayedID, "slam")
        store.apply([RadioTrack(stationID: "slam", artist: "", title: "On air",
                                identity: "slam-onair", eventAt: nil, isSong: false)], group: .slam)
        XCTAssertFalse(store.hasReadyMetadata)
        XCTAssertNil(store.displayedID)
    }

    func testWingsResizeWithMenuSpaceAndHideWhenItIsGone() {
        let roomy = WingLayout.fit(leftDesired: 320, rightDesired: 145,
                                   leftCapacity: 370, rightCapacity: 180)
        XCTAssertEqual(roomy?.left, 320)
        XCTAssertEqual(roomy?.right, 145)

        let crowded = WingLayout.fit(leftDesired: 320, rightDesired: 145,
                                     leftCapacity: 170, rightCapacity: 90)
        XCTAssertEqual(crowded?.left, 170)
        XCTAssertEqual(crowded?.right, 90)
        XCTAssertNil(WingLayout.fit(leftDesired: 320, rightDesired: 145,
                                    leftCapacity: 79, rightCapacity: 90))
    }

    private func track(_ title: String, stationID: String = "slam", eventAt: Date? = nil) -> RadioTrack {
        RadioTrack(stationID: stationID, artist: "Artist", title: title,
                   identity: title, eventAt: eventAt, isSong: true)
    }
}
