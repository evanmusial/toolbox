import Combine
import Foundation

enum HUDMotion {
    case none
    case dissolve
    case glideWithTint
}

@MainActor
final class RadioStore: ObservableObject {
    @Published private(set) var enabledIDs: Set<String>
    @Published private(set) var newTrackAnimationIDs: Set<String>
    @Published private(set) var tracks: [String: RadioTrack] = [:]
    @Published private(set) var lastUpdated: [String: Date] = [:]
    @Published private(set) var errors: [FeedGroup: String] = [:]
    @Published private(set) var displayedID: String?
    @Published private(set) var revision = 0
    @Published private(set) var motion: HUDMotion = .dissolve
    @Published private(set) var hasReadyMetadata = false
    @Published var cycleSeconds: Double {
        didSet {
            cycleSeconds = min(30, max(2, cycleSeconds))
            defaults.set(cycleSeconds, forKey: "cycleSeconds")
            nextCycleAt = Date().addingTimeInterval(cycleSeconds)
        }
    }
    @Published var hudVisible: Bool {
        didSet { defaults.set(hudVisible, forKey: "hudVisible") }
    }

    private let defaults: UserDefaults
    private var pollTasks: [FeedGroup: Task<Void, Never>] = [:]
    private var inFlight: Set<FeedGroup> = []
    private var etags: [FeedGroup: String] = [:]
    private var timer: Timer?
    private var pendingUpdates: [String] = []
    private var holdUntil = Date.distantPast
    private var nextCycleAt = Date.distantPast

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        let known = Set(RadioStation.all.map(\.id))
        if let saved = defaults.array(forKey: "enabledStationIDs") as? [String] {
            enabledIDs = Set(saved).intersection(known)
        } else {
            enabledIDs = known
        }
        let savedAnimations = defaults.array(forKey: "newTrackAnimationIDs") as? [String] ?? []
        newTrackAnimationIDs = Set(savedAnimations).intersection(known)
        let savedCycle = defaults.object(forKey: "cycleSeconds") as? Double ?? 5
        cycleSeconds = min(30, max(2, savedCycle))
        hudVisible = defaults.object(forKey: "hudVisible") as? Bool ?? true
    }

    var enabledStations: [RadioStation] {
        RadioStation.all.filter { enabledIDs.contains($0.id) }
    }

    var readyStations: [RadioStation] {
        enabledStations.filter { isReady($0) }
    }

    var displayedStation: RadioStation? {
        guard let displayedID else { return nil }
        return RadioStation.all.first { $0.id == displayedID }
    }

    var displayedText: String {
        guard let station = displayedStation else { return "Choose a station" }
        return text(for: station)
    }

    func text(for station: RadioStation) -> String {
        guard let track = tracks[station.id] else { return "Waiting for metadata" }
        if station.group == .siriusXM,
           let eventAt = track.eventAt,
           Date().timeIntervalSince(eventAt) >= 20 * 60 {
            return "Metadata delayed"
        }
        return track.text
    }

    func isEnabled(_ station: RadioStation) -> Bool { enabledIDs.contains(station.id) }

    func animatesNewTracks(for station: RadioStation) -> Bool {
        newTrackAnimationIDs.contains(station.id)
    }

    func setNewTrackAnimationEnabled(_ enabled: Bool, for station: RadioStation) {
        if enabled { newTrackAnimationIDs.insert(station.id) }
        else {
            newTrackAnimationIDs.remove(station.id)
            pendingUpdates.removeAll { $0 == station.id }
        }
        defaults.set(RadioStation.all.filter { newTrackAnimationIDs.contains($0.id) }.map(\.id),
                     forKey: "newTrackAnimationIDs")
    }

    func setEnabled(_ enabled: Bool, for station: RadioStation) {
        if enabled { enabledIDs.insert(station.id) }
        else { enabledIDs.remove(station.id) }
        defaults.set(RadioStation.all.filter { enabledIDs.contains($0.id) }.map(\.id), forKey: "enabledStationIDs")
        pendingUpdates.removeAll { !enabledIDs.contains($0) }
        refreshReadyState()
        restartPolling()
    }

    func start() {
        refreshReadyState()
        let timer = Timer(timeInterval: 0.25, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.tick() }
        }
        RunLoop.main.add(timer, forMode: .common)
        self.timer = timer
        restartPolling()
    }

    func stop() {
        timer?.invalidate()
        timer = nil
        for task in pollTasks.values { task.cancel() }
        pollTasks.removeAll()
    }

    func refreshNow() {
        for group in Set(enabledStations.map(\.group)) {
            Task { _ = await poll(group) }
        }
    }

    private func restartPolling() {
        for task in pollTasks.values { task.cancel() }
        pollTasks.removeAll()
        for group in Set(enabledStations.map(\.group)) {
            pollTasks[group] = Task { [weak self] in
                while !Task.isCancelled {
                    guard let self else { break }
                    let delay = await self.poll(group)
                    if Task.isCancelled { break }
                    try? await Task.sleep(nanoseconds: UInt64(max(1, delay) * 1_000_000_000))
                }
            }
        }
    }

    private func poll(_ group: FeedGroup) async -> TimeInterval {
        guard !inFlight.contains(group) else { return group.minimumInterval }
        inFlight.insert(group)
        defer { inFlight.remove(group) }

        var request = URLRequest(url: group.url)
        request.timeoutInterval = 15
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.setValue("Wavewing/1.0 (macOS; now-playing display)", forHTTPHeaderField: "User-Agent")
        request.setValue("application/json, text/xml, text/plain, text/html", forHTTPHeaderField: "Accept")
        if let etag = etags[group] { request.setValue(etag, forHTTPHeaderField: "If-None-Match") }

        do {
            let data: Data
            let http: HTTPURLResponse
            do {
                let (received, response) = try await URLSession.shared.data(for: request)
                guard let response = response as? HTTPURLResponse else { throw MetadataError.invalidResponse }
                data = received
                http = response
            } catch let error as URLError where group == .my959 && error.code == .secureConnectionFailed {
                // Icecast's TLS setup works with system curl but fails CFNetwork's handshake.
                // Keep certificate verification enabled and use curl only for this source.
                data = try await curlVerified(group.url)
                guard let response = HTTPURLResponse(url: group.url, statusCode: 200,
                                                     httpVersion: "HTTP/1.1", headerFields: [:]) else {
                    throw MetadataError.invalidResponse
                }
                http = response
            }
            if http.statusCode == 304 {
                errors[group] = nil
                return max(group.minimumInterval, cacheAge(http) ?? 0)
            }
            if http.statusCode == 429 || http.statusCode == 503 {
                errors[group] = "Source busy (\(http.statusCode))"
                return max(group.minimumInterval * 2, retryAfter(http) ?? 0)
            }
            guard http.statusCode == 200 else {
                errors[group] = "HTTP \(http.statusCode)"
                return group.minimumInterval * 2
            }
            guard data.count <= (group == .siriusXM ? 4_000_000 : 1_000_000) else {
                throw MetadataError.invalidResponse
            }
            if let etag = http.value(forHTTPHeaderField: "ETag") { etags[group] = etag }
            let parsed = try RadioMetadata.parse(group, data: data)
            apply(parsed, group: group)
            errors[group] = nil
            return max(group.minimumInterval, cacheAge(http) ?? 0)
        } catch {
            if !Task.isCancelled {
                if let error = error as? URLError { errors[group] = error.localizedDescription }
                else if case MetadataError.noSong = error { errors[group] = "No song metadata" }
                else { errors[group] = "Source unavailable" }
            }
            return group.minimumInterval * 2
        }
    }

    private func curlVerified(_ url: URL) async throws -> Data {
        try await Task.detached(priority: .utility) {
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/usr/bin/curl")
            process.arguments = ["-fsS", "--compressed", "--max-time", "12", "--max-filesize", "1000000",
                                 "-A", "Wavewing/1.0 (macOS; now-playing display)", url.absoluteString]
            let output = Pipe()
            process.standardOutput = output
            process.standardError = FileHandle.nullDevice
            try process.run()
            let data = output.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            guard process.terminationStatus == 0, !data.isEmpty else { throw MetadataError.invalidResponse }
            return data
        }.value
    }

    func apply(_ incoming: [RadioTrack], group: FeedGroup) {
        let now = Date()
        var changed: [RadioTrack] = []
        for track in incoming where enabledIDs.contains(track.stationID) {
            let old = tracks[track.stationID]
            tracks[track.stationID] = track
            lastUpdated[track.stationID] = now
            if let old, old.identity != track.identity, track.isSong,
               newTrackAnimationIDs.contains(track.stationID) {
                changed.append(track)
            } else if displayedID == track.stationID, track.isSong,
                      old == nil || old?.identity != track.identity {
                show(track.stationID, motion: .none)
            }
        }
        refreshReadyState()
        changed.sort { ($0.eventAt ?? now) > ($1.eventAt ?? now) }
        for track in changed where !pendingUpdates.contains(track.stationID) {
            pendingUpdates.append(track.stationID)
        }
        if now >= holdUntil, !pendingUpdates.isEmpty {
            showNextUpdate()
        }
    }

    private func tick() {
        refreshReadyState()
        let now = Date()
        guard now >= holdUntil else { return }
        if !pendingUpdates.isEmpty {
            showNextUpdate()
            return
        }
        guard now >= nextCycleAt else { return }
        let stations = readyStations
        guard !stations.isEmpty else { return }
        let index = stations.firstIndex { $0.id == displayedID } ?? -1
        show(stations[(index + 1) % stations.count].id, motion: .dissolve)
    }

    private func showNextUpdate() {
        while !pendingUpdates.isEmpty {
            let stationID = pendingUpdates.removeFirst()
            guard let station = RadioStation.all.first(where: { $0.id == stationID }),
                  isReady(station), enabledIDs.contains(stationID) else { continue }
            show(stationID, motion: .glideWithTint)
            holdUntil = Date().addingTimeInterval(max(6, cycleSeconds))
            nextCycleAt = holdUntil
            break
        }
    }

    private func show(_ stationID: String?, motion: HUDMotion) {
        displayedID = stationID
        self.motion = motion
        revision += 1
        nextCycleAt = Date().addingTimeInterval(cycleSeconds)
    }

    private func isReady(_ station: RadioStation) -> Bool {
        guard let track = tracks[station.id], track.isSong else { return false }
        if station.group == .siriusXM, let eventAt = track.eventAt,
           Date().timeIntervalSince(eventAt) >= 20 * 60 {
            return false
        }
        return true
    }

    private func refreshReadyState() {
        let ready = readyStations
        pendingUpdates.removeAll { stationID in
            !ready.contains { $0.id == stationID }
        }
        if !ready.contains(where: { $0.id == displayedID }) {
            let fallback = ready.first?.id
            if displayedID != fallback { show(fallback, motion: .none) }
        }
        let available = !ready.isEmpty
        if hasReadyMetadata != available { hasReadyMetadata = available }
    }

    private func cacheAge(_ response: HTTPURLResponse) -> TimeInterval? {
        guard let header = response.value(forHTTPHeaderField: "Cache-Control"),
              let range = header.range(of: #"max-age\s*=\s*(\d+)"#, options: .regularExpression) else { return nil }
        let digits = header[range].split(separator: "=").last?.trimmingCharacters(in: .whitespaces)
        return digits.flatMap(Double.init)
    }

    private func retryAfter(_ response: HTTPURLResponse) -> TimeInterval? {
        guard let header = response.value(forHTTPHeaderField: "Retry-After") else { return nil }
        if let seconds = Double(header) { return seconds }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = "EEE, dd MMM yyyy HH:mm:ss 'GMT'"
        return formatter.date(from: header).map { max(0, $0.timeIntervalSinceNow) }
    }
}
