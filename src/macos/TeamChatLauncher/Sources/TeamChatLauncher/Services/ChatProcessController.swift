import AppKit
import Darwin
import Foundation

@MainActor
final class ChatProcessController: ObservableObject {
    @Published private(set) var state: ChatProcessState = .notConfigured
    @Published private(set) var pid: Int32?
    @Published private(set) var startedAt: Date?
    @Published private(set) var exitCode: Int32?
    @Published private(set) var lastError: String?
    @Published private(set) var logs: [LogEntry] = []

    private var process: Process?
    private var logHandle: FileHandle?
    private var stdoutBuffer = ""
    private var stderrBuffer = ""

    var isRunning: Bool {
        state == .launching || state == .running || state == .stopping
    }

    func start(
        profile: ProfileSummary,
        settings: ProfileLauncherSettings,
        launchTicket: String?,
        config: GlobalLauncherConfig
    ) throws {
        guard !config.projectRoot.isEmpty else {
            state = .notConfigured
            throw ControlCLIError.missingProjectRoot
        }
        guard !isRunning else { return }

        let pythonArgs = PythonProcessBuilder.chatClientArguments(
            profile: profile.profile,
            projectRoot: config.projectRoot,
            settings: settings,
            launchTicket: launchTicket
        )
        let spec = PythonProcessBuilder.makeProcessSpec(
            pythonExecutable: config.pythonExecutable,
            pythonArguments: pythonArgs
        )

        let process = Process()
        process.executableURL = spec.executableURL
        process.arguments = spec.arguments
        process.currentDirectoryURL = URL(fileURLWithPath: config.projectRoot)

        var env = ProcessInfo.processInfo.environment
        env["PYTHONPATH"] = config.projectRoot
        env["PYTHONUNBUFFERED"] = "1"
        process.environment = env

        let stdout = Pipe()
        let stderr = Pipe()
        process.standardOutput = stdout
        process.standardError = stderr

        let logURL = try makeLogFileURL(profile: profile.profile, projectRoot: config.projectRoot)
        logHandle = try FileHandle(forWritingTo: logURL)

        stdout.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else { return }
            Task { @MainActor in
                self?.appendOutput(data, stream: "stdout")
            }
        }
        stderr.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else { return }
            Task { @MainActor in
                self?.appendOutput(data, stream: "stderr")
            }
        }

        process.terminationHandler = { [weak self] finished in
            stdout.fileHandleForReading.readabilityHandler = nil
            stderr.fileHandleForReading.readabilityHandler = nil
            Task { @MainActor in
                self?.finishProcess(finished)
            }
        }

        state = .launching
        lastError = nil
        exitCode = nil
        logs.removeAll(keepingCapacity: true)
        try process.run()
        self.process = process
        pid = process.processIdentifier
        startedAt = Date()
        state = .running
    }

    func stop() {
        guard let process, process.isRunning else {
            state = .stopped
            return
        }
        state = .stopping
        process.terminate()

        Task { [weak self] in
            try? await Task.sleep(nanoseconds: 3_000_000_000)
            await MainActor.run {
                guard let self, let process = self.process, process.isRunning else { return }
                Darwin.kill(process.processIdentifier, SIGKILL)
            }
        }
    }

    func clearLogs() {
        logs.removeAll()
    }

    private func appendOutput(_ data: Data, stream: String) {
        logHandle?.write(data)
        let text = String(data: data, encoding: .utf8) ?? ""
        var buffer = stream == "stdout" ? stdoutBuffer : stderrBuffer
        buffer += text

        let lines = buffer.split(separator: "\n", omittingEmptySubsequences: false)
        let completeLines = lines.dropLast()
        buffer = String(lines.last ?? "")
        if stream == "stdout" {
            stdoutBuffer = buffer
        } else {
            stderrBuffer = buffer
        }

        for line in completeLines {
            appendLine(String(line), stream: stream)
        }
    }

    private func appendLine(_ line: String, stream: String) {
        let event = parseLauncherEvent(line)
        if let event, event.type == "fatal_error" {
            lastError = event.error
        }
        logs.append(LogEntry(date: Date(), stream: stream, text: line, event: event))
        if logs.count > 2_000 {
            logs.removeFirst(logs.count - 2_000)
        }
    }

    private func finishProcess(_ finished: Process) {
        exitCode = finished.terminationStatus
        pid = nil
        process = nil
        try? logHandle?.close()
        logHandle = nil
        state = finished.terminationStatus == 0 ? .exited : .failed
        if finished.terminationStatus != 0, lastError == nil {
            lastError = "Python 客户端退出码 \(finished.terminationStatus)"
        }
    }

    private func makeLogFileURL(profile: String, projectRoot: String) throws -> URL {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyyMMdd-HHmmss"
        let logsDir = URL(fileURLWithPath: projectRoot)
            .appendingPathComponent("runtime/profiles/\(profile)/logs", isDirectory: true)
        try FileManager.default.createDirectory(at: logsDir, withIntermediateDirectories: true)
        let url = logsDir.appendingPathComponent("client-\(formatter.string(from: Date())).log")
        FileManager.default.createFile(atPath: url.path, contents: nil)
        return url
    }

    private func parseLauncherEvent(_ line: String) -> LauncherEvent? {
        let prefix = "IMT_EVENT "
        guard line.hasPrefix(prefix) else { return nil }
        let jsonText = String(line.dropFirst(prefix.count))
        guard let data = jsonText.data(using: .utf8) else { return nil }
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try? decoder.decode(LauncherEvent.self, from: data)
    }
}
