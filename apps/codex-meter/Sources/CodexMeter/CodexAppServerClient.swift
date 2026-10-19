import Foundation

enum CodexClientError: LocalizedError {
    case binaryNotFound
    case serverStopped(String?)
    case protocolError(String)

    var errorDescription: String? {
        switch self {
        case .binaryNotFound:
            return "Codex CLI was not found. Install Codex or add it to PATH."
        case .serverStopped(let details):
            if let details, !details.isEmpty {
                return "Codex app-server stopped: \(details)"
            }
            return "Codex app-server stopped unexpectedly."
        case .protocolError(let message):
            return message
        }
    }
}

final class CodexAppServerClient {
    typealias SnapshotHandler = (RateLimitResponse) -> Void
    typealias ErrorHandler = (Error) -> Void

    private let queue = DispatchQueue(label: "io.openai.codex-meter.app-server")
    private var process: Process?
    private var inputHandle: FileHandle?
    private var outputHandle: FileHandle?
    private var errorHandle: FileHandle?
    private var outputBuffer = Data()
    private var errorBuffer = Data()
    private var initialized = false
    private var refreshPending = true
    private var nextRequestId = 100
    private var snapshotHandler: SnapshotHandler?
    private var errorHandler: ErrorHandler?

    var isRunning: Bool {
        process?.isRunning == true
    }

    func start(onSnapshot: @escaping SnapshotHandler, onError: @escaping ErrorHandler) throws {
        guard !isRunning else { return }
        guard let binary = Self.findCodexBinary() else {
            throw CodexClientError.binaryNotFound
        }

        snapshotHandler = onSnapshot
        errorHandler = onError
        outputBuffer.removeAll(keepingCapacity: true)
        errorBuffer.removeAll(keepingCapacity: true)
        initialized = false
        refreshPending = true

        let process = Process()
        let input = Pipe()
        let output = Pipe()
        let errors = Pipe()

        process.executableURL = URL(fileURLWithPath: binary)
        process.arguments = ["app-server"]
        process.standardInput = input
        process.standardOutput = output
        process.standardError = errors
        process.environment = Self.augmentedEnvironment()

        inputHandle = input.fileHandleForWriting
        outputHandle = output.fileHandleForReading
        errorHandle = errors.fileHandleForReading
        self.process = process

        output.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else { return }
            self?.queue.async { self?.consumeOutput(data) }
        }

        errors.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else { return }
            self?.queue.async { self?.consumeError(data) }
        }

        process.terminationHandler = { [weak self] process in
            guard let self else { return }
            self.queue.async {
                let details = String(data: self.errorBuffer.suffix(2_000), encoding: .utf8)?
                    .trimmingCharacters(in: .whitespacesAndNewlines)
                self.initialized = false
                if process.terminationStatus != 0 {
                    self.errorHandler?(CodexClientError.serverStopped(details))
                }
            }
        }

        do {
            try process.run()
        } catch {
            cleanUp()
            throw error
        }

        queue.async { [weak self] in
            self?.send([
                "method": "initialize",
                "id": 1,
                "params": [
                    "clientInfo": [
                        "name": "codex_meter",
                        "title": "Codex Meter",
                        "version": "0.1.0"
                    ]
                ]
            ])
        }
    }

    func requestRateLimits() {
        queue.async { [weak self] in
            guard let self else { return }
            guard self.initialized else {
                self.refreshPending = true
                return
            }
            self.sendRateLimitRequest()
        }
    }

    func stop() {
        outputHandle?.readabilityHandler = nil
        errorHandle?.readabilityHandler = nil
        if process?.isRunning == true {
            process?.terminate()
        }
        cleanUp()
    }

    private func cleanUp() {
        try? inputHandle?.close()
        try? outputHandle?.close()
        try? errorHandle?.close()
        inputHandle = nil
        outputHandle = nil
        errorHandle = nil
        process = nil
        initialized = false
    }

    private func consumeOutput(_ data: Data) {
        outputBuffer.append(data)

        while let newline = outputBuffer.firstIndex(of: 0x0A) {
            let line = Data(outputBuffer[..<newline])
            outputBuffer.removeSubrange(...newline)
            guard !line.isEmpty else { continue }
            handleMessage(line)
        }
    }

    private func consumeError(_ data: Data) {
        errorBuffer.append(data)
        if errorBuffer.count > 8_000 {
            errorBuffer = Data(errorBuffer.suffix(8_000))
        }
    }

    private func handleMessage(_ data: Data) {
        guard
            let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return }

        if let method = object["method"] as? String,
           method == "account/rateLimits/updated" {
            sendRateLimitRequest()
            return
        }

        if let error = object["error"] as? [String: Any] {
            let message = error["message"] as? String ?? "Codex returned an unknown error."
            errorHandler?(CodexClientError.protocolError(message))
            return
        }

        guard let id = object["id"] as? Int else { return }

        if id == 1 {
            initialized = true
            send(["method": "initialized", "params": [:]])
            if refreshPending {
                refreshPending = false
                sendRateLimitRequest()
            }
            return
        }

        guard id >= 100, let result = object["result"] else { return }

        do {
            let resultData = try JSONSerialization.data(withJSONObject: result)
            let snapshot = try JSONDecoder().decode(RateLimitResponse.self, from: resultData)
            snapshotHandler?(snapshot)
        } catch {
            errorHandler?(CodexClientError.protocolError("Could not read Codex usage data: \(error.localizedDescription)"))
        }
    }

    private func sendRateLimitRequest() {
        let id = nextRequestId
        nextRequestId += 1
        send(["method": "account/rateLimits/read", "id": id])
    }

    private func send(_ object: [String: Any]) {
        guard let inputHandle, process?.isRunning == true else { return }
        do {
            var data = try JSONSerialization.data(withJSONObject: object)
            data.append(0x0A)
            try inputHandle.write(contentsOf: data)
        } catch {
            errorHandler?(error)
        }
    }

    private static func findCodexBinary() -> String? {
        let environment = augmentedEnvironment()
        let pathEntries = (environment["PATH"] ?? "").split(separator: ":").map(String.init)
        let candidates = pathEntries.map { "\($0)/codex" } + [
            "/opt/homebrew/bin/codex",
            "/usr/local/bin/codex"
        ]

        return candidates.first { FileManager.default.isExecutableFile(atPath: $0) }
    }

    private static func augmentedEnvironment() -> [String: String] {
        var environment = ProcessInfo.processInfo.environment
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let additions = [
            "/opt/homebrew/bin",
            "/usr/local/bin",
            "\(home)/.local/bin",
            "\(home)/bin"
        ]
        let existing = environment["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin"
        environment["PATH"] = (additions + [existing]).joined(separator: ":")
        return environment
    }
}
