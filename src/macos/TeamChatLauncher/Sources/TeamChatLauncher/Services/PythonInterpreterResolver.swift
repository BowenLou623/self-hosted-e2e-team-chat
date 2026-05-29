import Foundation

enum PythonInterpreterResolver {
    static func preferredExecutable() -> String {
        let candidates = [
            "/opt/homebrew/bin/python3",
            "/usr/local/bin/python3",
            "/Library/Frameworks/Python.framework/Versions/Current/bin/python3",
            "/usr/bin/python3",
            "/Applications/Xcode.app/Contents/Developer/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3"
        ]
        for candidate in candidates where FileManager.default.isExecutableFile(atPath: candidate) {
            return candidate
        }
        return "python3"
    }

    static func validate(executable: String, projectRoot: String) async -> PythonInterpreterStatus {
        await Task.detached(priority: .userInitiated) {
            let version = runPython(executable: executable, arguments: ["--version"], projectRoot: projectRoot)
            let control = runPython(
                executable: executable,
                arguments: ["-m", "src.app.control", "--project-root", projectRoot, "profile", "list"],
                projectRoot: projectRoot,
                environment: ["PYTHONPATH": projectRoot, "PYTHONUNBUFFERED": "1"]
            )
            let pySide = runPython(
                executable: executable,
                arguments: ["-c", "import PySide6; print('PySide6 OK')"],
                projectRoot: projectRoot
            )

            let versionText = firstUsefulLine(version.output).replacingOccurrences(of: "Python ", with: "")
            let controlOK = control.exitCode == 0
            let pySideOK = pySide.exitCode == 0

            let summary: String
            if controlOK && pySideOK {
                summary = "可用: Python \(versionText)"
            } else if controlOK {
                summary = "control 可用，但 PySide6 不可用"
            } else {
                summary = "control CLI 不可用"
            }

            let details = [
                "Python: \(version.output)",
                "control: \(control.output)",
                "PySide6: \(pySide.output)"
            ].joined(separator: "\n\n")

            return PythonInterpreterStatus(
                executable: executable,
                version: versionText,
                controlOK: controlOK,
                pySideOK: pySideOK,
                summary: summary,
                details: details
            )
        }.value
    }

    private static func runPython(
        executable: String,
        arguments: [String],
        projectRoot: String,
        environment: [String: String] = [:]
    ) -> CommandResult {
        let spec = PythonProcessBuilder.makeProcessSpec(
            pythonExecutable: executable,
            pythonArguments: arguments
        )
        let process = Process()
        process.executableURL = spec.executableURL
        process.arguments = spec.arguments
        if !projectRoot.isEmpty {
            process.currentDirectoryURL = URL(fileURLWithPath: projectRoot)
        }
        var env = ProcessInfo.processInfo.environment
        for (key, value) in environment {
            env[key] = value
        }
        process.environment = env

        let stdout = Pipe()
        let stderr = Pipe()
        process.standardOutput = stdout
        process.standardError = stderr

        do {
            try process.run()
            process.waitUntilExit()
            let out = String(data: stdout.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
            let err = String(data: stderr.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
            return CommandResult(exitCode: process.terminationStatus, output: compactOutput(out + err))
        } catch {
            return CommandResult(exitCode: 127, output: error.localizedDescription)
        }
    }

    private static func firstUsefulLine(_ text: String) -> String {
        text
            .split(whereSeparator: \.isNewline)
            .map(String.init)
            .first(where: { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }) ?? ""
    }

    static func compactOutput(_ text: String, maxLines: Int = 6, maxCharacters: Int = 900) -> String {
        let lines = text
            .split(whereSeparator: \.isNewline)
            .map(String.init)
            .filter { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
        var compact = lines.suffix(maxLines).joined(separator: "\n")
        if compact.count > maxCharacters {
            compact = String(compact.suffix(maxCharacters))
        }
        return compact
    }
}

private struct CommandResult {
    let exitCode: Int32
    let output: String
}
