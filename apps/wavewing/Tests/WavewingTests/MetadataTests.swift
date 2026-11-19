import XCTest
@testable import Wavewing

final class MetadataTests: XCTestCase {
    func testNominatedSourcesProduceArtistAndTitle() throws {
        let samples: [(FeedGroup, String, String, String)] = [
            (.slam, #"[{"title":"David Guetta - Say My Name"}]"#, "David Guetta", "Say My Name"),
            (.c895, #"<html><table><tr><td>Current <b>Song:</b></td><td class="streamdata">A &amp; B - Song</td></tr></table></html>"#, "A & B", "Song"),
            (.revolution935, "Carlita - WYWD (feat. Justine Skye) on Revolution 935", "Carlita", "WYWD (feat. Justine Skye)"),
            (.revolution997, "Falling Into You by MADISON & EMMA LX", "MADISON & EMMA LX", "Falling Into You"),
            (.kgay, "<playlist><programType>PGM</programType><artist>Milli Vanilli</artist><title>Baby Don't Forget My Number</title><programStartTS>18 Nov 2026 15:02:37</programStartTS></playlist>", "Milli Vanilli", "Baby Don't Forget My Number"),
            (.my959, #"{"icestats":{"source":{"title":"KAAZE - Psychological (feat. CERES)"}}}"#, "KAAZE", "Psychological (feat. CERES)"),
            (.pulse87, #"{"icestats":{"source":[{"listenurl":"http://example/other","title":"Wrong - Song"},{"listenurl":"http://example/PULSE-87","title":"Camera (CYRIL Remix) - Ed Sheeran"}]}}"#, "Ed Sheeran", "Camera (CYRIL Remix)"),
            (.party105, #"<nowplaying-info-list><nowplaying-info mountName="WPTYFM" timestamp="1795014256" type="track"><property name="cue_title">Thriller</property><property name="track_artist_name">Jackson, Michael</property></nowplaying-info></nowplaying-info-list>"#, "Jackson, Michael", "Thriller")
        ]
        for (group, body, artist, title) in samples {
            let parsed = try RadioMetadata.parse(group, data: Data(body.utf8))
            XCTAssertEqual(parsed.count, 1, "\(group)")
            XCTAssertEqual(parsed[0].artist, artist, "\(group)")
            XCTAssertEqual(parsed[0].title, title, "\(group)")
            XCTAssertTrue(parsed[0].isSong, "\(group)")
        }
    }

    func testSharedSiriusXMFeedSelectsFourStations() throws {
        let now = Date(timeIntervalSince1970: 1_795_014_700)
        let entries = ["bpm", "diplosrevolution", "siriusxmchill", "utopia", "other"].enumerated().map { index, channel in
            #"{"id":"event-\#(index)","channelId":"\#(channel)","timestamp":"2026-11-18T15:10:00.000Z","track":{"title":"Song \#(index)","artists":["Artist"]}}"#
        }.joined(separator: ",")
        let body = #"{"results":[\#(entries)]}"#
        let tracks = try RadioMetadata.parse(.siriusXM, data: Data(body.utf8), now: now)
        XCTAssertEqual(Set(tracks.map(\.stationID)), Set(["bpm", "diplosrevolution", "siriusxmchill", "utopia"]))
    }

    func testPartyAdIsNotAnnouncedAsNewSong() throws {
        let body = #"<nowplaying-info-list><nowplaying-info timestamp="1795014103" type="ad"><property name="cue_title">Commercial</property></nowplaying-info></nowplaying-info-list>"#
        let event = try XCTUnwrap(RadioMetadata.parse(.party105, data: Data(body.utf8)).first)
        XCTAssertFalse(event.isSong)
        XCTAssertEqual(event.text, "On air")
    }

    func testEmptyRevolutionResponseShowsOnAirWithoutSongAlert() throws {
        let event = try XCTUnwrap(RadioMetadata.parse(.revolution997, data: Data()).first)
        XCTAssertFalse(event.isSong)
        XCTAssertEqual(event.text, "On air")
    }
}
