import Foundation

enum FeedGroup: String, CaseIterable {
    case slam, c895, siriusXM, revolution935, revolution997, kgay, my959, pulse87, party105

    var url: URL {
        let address: String
        switch self {
        case .slam:
            address = "https://prod.radio-api.net/stations/now-playing?stationIds=slam"
        case .c895:
            address = "http://knhc-ice.streamguys1.com/status.xsl?mount=/live"
        case .siriusXM:
            address = "https://xmplaylist.com/api/feed"
        case .revolution935:
            address = "https://2radio.shoutcastservices.com/proxy/rev935/currentsong"
        case .revolution997:
            address = "https://centova87.shoutcastservices.com/proxy/revolution997/currentsong"
        case .kgay:
            address = "https://streamdb9web.securenetsystems.net/player_status_update/KGAY.xml"
        case .my959:
            address = "https://kxrglp.vdi.gocloudservices.com/status-json.xsl"
        case .pulse87:
            address = "https://s2.yesstreaming.net:17039/status-json.xsl"
        case .party105:
            address = "https://np.tritondigital.com/public/nowplaying?mountName=WPTYFM&numberToFetch=1"
        }
        return URL(string: address)!
    }

    var minimumInterval: TimeInterval {
        switch self {
        case .siriusXM: return 120
        case .slam, .c895, .revolution935, .revolution997: return 30
        case .kgay, .my959, .pulse87, .party105: return 60
        }
    }
}

struct RadioStation: Identifiable, Equatable {
    let id: String
    let name: String
    let group: FeedGroup

    static let all: [RadioStation] = [
        .init(id: "slam", name: "SLAM", group: .slam),
        .init(id: "c895", name: "C89.5", group: .c895),
        .init(id: "bpm", name: "BPM", group: .siriusXM),
        .init(id: "diplosrevolution", name: "Diplo’s Revolution", group: .siriusXM),
        .init(id: "siriusxmchill", name: "SiriusXM Chill", group: .siriusXM),
        .init(id: "utopia", name: "Utopia", group: .siriusXM),
        .init(id: "revolution935", name: "Revolution 93.5", group: .revolution935),
        .init(id: "revolution997", name: "Revolution 99.7", group: .revolution997),
        .init(id: "kgay", name: "KGAY", group: .kgay),
        .init(id: "my959", name: "My959", group: .my959),
        .init(id: "pulse87", name: "Pulse 87", group: .pulse87),
        .init(id: "party105", name: "Party 105", group: .party105)
    ]
}

struct RadioTrack: Equatable {
    let stationID: String
    let artist: String
    let title: String
    let identity: String
    let eventAt: Date?
    let isSong: Bool

    var text: String {
        artist.isEmpty ? title : "\(artist) - \(title)"
    }
}

enum MetadataError: Error {
    case invalidResponse
    case noSong
}

enum RadioMetadata {
    static func parse(_ group: FeedGroup, data: Data, now: Date = Date()) throws -> [RadioTrack] {
        switch group {
        case .slam: return [try slam(data)]
        case .c895: return [try c895(data)]
        case .siriusXM: return try siriusXM(data, now: now)
        case .revolution935: return [try revolution935(data)]
        case .revolution997: return [try revolution997(data)]
        case .kgay: return [try kgay(data)]
        case .my959: return [try icecast(data, stationID: "my959", mount: nil, reversed: false)]
        case .pulse87: return [try icecast(data, stationID: "pulse87", mount: "/PULSE-87", reversed: true)]
        case .party105: return [try party105(data)]
        }
    }

    private static func song(_ station: String, _ artist: String, _ title: String, id: String? = nil, at: Date? = nil) throws -> RadioTrack {
        let a = artist.trimmingCharacters(in: .whitespacesAndNewlines)
        let t = title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !a.isEmpty, !t.isEmpty else { throw MetadataError.noSong }
        return RadioTrack(stationID: station, artist: a, title: t, identity: id ?? "\(a)\u{0}\(t)", eventAt: at, isSong: true)
    }

    private static func combined(_ station: String, _ value: String, reversed: Bool = false) throws -> RadioTrack {
        let clean = htmlText(value)
        guard let range = clean.range(of: " - ", options: reversed ? .backwards : []) else {
            throw MetadataError.noSong
        }
        let left = String(clean[..<range.lowerBound])
        let right = String(clean[range.upperBound...])
        return try reversed ? song(station, right, left) : song(station, left, right)
    }

    private static func slam(_ data: Data) throws -> RadioTrack {
        guard let array = try JSONSerialization.jsonObject(with: data) as? [[String: Any]],
              let raw = array.first?["title"] as? String else { throw MetadataError.invalidResponse }
        return try combined("slam", raw)
    }

    private static func c895(_ data: Data) throws -> RadioTrack {
        guard let source = String(data: data, encoding: .utf8) else { throw MetadataError.invalidResponse }
        let rows = matches(#"(?is)<tr\b[^>]*>(.*?)</tr>"#, in: source)
        for row in rows {
            let cells = matches(#"(?is)<td\b[^>]*>(.*?)</td>"#, in: row)
            guard cells.count >= 2 else { continue }
            for index in 0..<(cells.count - 1) {
                if htmlText(cells[index]).lowercased().replacingOccurrences(of: ":", with: "") == "current song" {
                    return try combined("c895", htmlText(cells[index + 1]))
                }
            }
        }
        throw MetadataError.noSong
    }

    private static func siriusXM(_ data: Data, now: Date) throws -> [RadioTrack] {
        guard let root = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let results = root["results"] as? [[String: Any]] else { throw MetadataError.invalidResponse }
        let wanted = Set(RadioStation.all.filter { $0.group == .siriusXM }.map(\.id))
        var latest: [String: RadioTrack] = [:]
        for item in results {
            guard let channel = item["channelId"] as? String, wanted.contains(channel),
                  let fields = item["track"] as? [String: Any],
                  let title = fields["title"] as? String,
                  let artists = fields["artists"] as? [String],
                  let eventID = item["id"] as? String,
                  let timestamp = item["timestamp"] as? String,
                  let eventAt = isoDate(timestamp) else { continue }
            // xmplaylist can stop reporting during DJ sets. An old play is not current metadata.
            guard now.timeIntervalSince(eventAt) < 20 * 60 else { continue }
            if let track = try? song(channel, artists.joined(separator: ", "), title, id: eventID, at: eventAt),
               latest[channel]?.eventAt.map({ eventAt > $0 }) ?? true {
                latest[channel] = track
            }
        }
        return Array(latest.values)
    }

    private static func revolution935(_ data: Data) throws -> RadioTrack {
        guard var raw = String(data: data, encoding: .utf8) else { throw MetadataError.invalidResponse }
        raw = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if raw.isEmpty { return offAir("revolution935") }
        if let suffix = raw.range(of: " on Revolution 935", options: [.caseInsensitive, .backwards]),
           suffix.upperBound == raw.endIndex {
            raw.removeSubrange(suffix)
        }
        return try combined("revolution935", raw)
    }

    private static func revolution997(_ data: Data) throws -> RadioTrack {
        guard let raw = String(data: data, encoding: .utf8) else { throw MetadataError.invalidResponse }
        let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if text.isEmpty { return offAir("revolution997") }
        if text.contains(" - ") { return try combined("revolution997", text) }
        if let range = text.range(of: " by ", options: [.caseInsensitive, .backwards]) {
            return try song("revolution997", String(text[range.upperBound...]), String(text[..<range.lowerBound]))
        }
        return try combined("revolution997", text)
    }

    private static func kgay(_ data: Data) throws -> RadioTrack {
        let xml = SimpleXML(data: data)
        guard xml.parse() else { throw MetadataError.invalidResponse }
        guard xml.values["programType"] == "PGM" else {
            return RadioTrack(stationID: "kgay", artist: "", title: "On air", identity: "kgay-program", eventAt: nil, isSong: false)
        }
        return try song("kgay", xml.values["artist"] ?? "", xml.values["title"] ?? "",
                        id: xml.values["programStartTS"])
    }

    private static func icecast(_ data: Data, stationID: String, mount: String?, reversed: Bool) throws -> RadioTrack {
        guard let root = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let stats = root["icestats"] as? [String: Any] else { throw MetadataError.invalidResponse }
        let sources: [[String: Any]]
        if let list = stats["source"] as? [[String: Any]] { sources = list }
        else if let single = stats["source"] as? [String: Any] { sources = [single] }
        else { throw MetadataError.invalidResponse }
        guard let source = sources.first(where: {
            guard let mount else { return true }
            return ($0["listenurl"] as? String)?.hasSuffix(mount) == true
        }), let text = source["title"] as? String else { throw MetadataError.noSong }
        return try combined(stationID, text, reversed: reversed)
    }

    private static func party105(_ data: Data) throws -> RadioTrack {
        let xml = TritonXML(data: data)
        guard xml.parse(), let event = xml.first else { throw MetadataError.invalidResponse }
        let at = event.timestamp.flatMap(Double.init).map(Date.init(timeIntervalSince1970:))
        guard event.type == "track" else {
            return RadioTrack(stationID: "party105", artist: "", title: "On air",
                              identity: "party-\(event.type)", eventAt: at, isSong: false)
        }
        return try song("party105", event.properties["track_artist_name"] ?? "",
                        event.properties["cue_title"] ?? "", id: event.timestamp, at: at)
    }

    private static func isoDate(_ text: String) -> Date? {
        let parser = ISO8601DateFormatter()
        parser.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return parser.date(from: text) ?? ISO8601DateFormatter().date(from: text)
    }

    private static func offAir(_ stationID: String) -> RadioTrack {
        RadioTrack(stationID: stationID, artist: "", title: "On air",
                   identity: "\(stationID)-onair", eventAt: nil, isSong: false)
    }

    private static func matches(_ pattern: String, in source: String) -> [String] {
        guard let regex = try? NSRegularExpression(pattern: pattern) else { return [] }
        let ns = source as NSString
        return regex.matches(in: source, range: NSRange(location: 0, length: ns.length)).compactMap {
            $0.range(at: 1).location == NSNotFound ? nil : ns.substring(with: $0.range(at: 1))
        }
    }

    private static func htmlText(_ source: String) -> String {
        let plain = source.replacingOccurrences(of: #"<[^>]+>"#, with: " ", options: .regularExpression)
            .replacingOccurrences(of: "&amp;", with: "&")
            .replacingOccurrences(of: "&quot;", with: "\"")
            .replacingOccurrences(of: "&#39;", with: "'")
            .replacingOccurrences(of: "&apos;", with: "'")
            .replacingOccurrences(of: "&nbsp;", with: " ")
        return plain.split(whereSeparator: \.isWhitespace).joined(separator: " ")
    }
}

private final class SimpleXML: NSObject, XMLParserDelegate {
    private let parser: XMLParser
    private var buffer = ""
    var values: [String: String] = [:]

    init(data: Data) {
        parser = XMLParser(data: data)
        super.init()
        parser.delegate = self
    }
    func parse() -> Bool { parser.parse() }
    func parser(_ parser: XMLParser, didStartElement elementName: String, namespaceURI: String?,
                qualifiedName qName: String?, attributes attributeDict: [String: String] = [:]) {
        buffer = ""
    }
    func parser(_ parser: XMLParser, foundCharacters string: String) { buffer += string }
    func parser(_ parser: XMLParser, didEndElement elementName: String, namespaceURI: String?,
                qualifiedName qName: String?) {
        values[elementName] = buffer.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

private final class TritonXML: NSObject, XMLParserDelegate {
    struct Event {
        let type: String
        let timestamp: String?
        var properties: [String: String] = [:]
    }
    private let parser: XMLParser
    private var propertyName: String?
    private var inFirstEvent = false
    private var buffer = ""
    var first: Event?

    init(data: Data) {
        parser = XMLParser(data: data)
        super.init()
        parser.delegate = self
    }
    func parse() -> Bool { parser.parse() }
    func parser(_ parser: XMLParser, didStartElement elementName: String, namespaceURI: String?,
                qualifiedName qName: String?, attributes attributeDict: [String: String] = [:]) {
        if elementName == "nowplaying-info", first == nil {
            first = Event(type: attributeDict["type"] ?? "", timestamp: attributeDict["timestamp"])
            inFirstEvent = true
        } else if elementName == "property", inFirstEvent {
            propertyName = attributeDict["name"]
            buffer = ""
        }
    }
    func parser(_ parser: XMLParser, foundCharacters string: String) { buffer += string }
    func parser(_ parser: XMLParser, didEndElement elementName: String, namespaceURI: String?,
                qualifiedName qName: String?) {
        if elementName == "property", let propertyName {
            first?.properties[propertyName] = buffer.trimmingCharacters(in: .whitespacesAndNewlines)
            self.propertyName = nil
        } else if elementName == "nowplaying-info" {
            inFirstEvent = false
        }
    }
}
