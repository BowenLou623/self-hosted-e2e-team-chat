import Foundation

struct PythonProcessSpec: Equatable {
    let executableURL: URL
    let arguments: [String]
}

enum PythonProcessBuilder {
    static func makeProcessSpec(pythonExecutable: String, pythonArguments: [String]) -> PythonProcessSpec {
        if pythonExecutable.contains("/") {
            return PythonProcessSpec(
                executableURL: URL(fileURLWithPath: pythonExecutable),
                arguments: pythonArguments
            )
        }
        return PythonProcessSpec(
            executableURL: URL(fileURLWithPath: "/usr/bin/env"),
            arguments: [pythonExecutable] + pythonArguments
        )
    }

    static func chatClientArguments(
        profile: String,
        projectRoot: String,
        settings: ProfileLauncherSettings,
        launchTicket: String?
    ) -> [String] {
        let dataDir = URL(fileURLWithPath: projectRoot)
            .appendingPathComponent("runtime/profiles/\(profile)")
            .path
        var args = [
            "-m", "src.app.main",
            "--profile", profile,
            "--transport", settings.transport,
            "--hub", settings.hubAddress,
            "--data-dir", dataDir,
            "--db-path", URL(fileURLWithPath: dataDir).appendingPathComponent("chat.db").path,
            "--config-dir", URL(fileURLWithPath: dataDir).appendingPathComponent("config").path,
            "--log-level", settings.logLevel
        ]
        if let launchTicket, !launchTicket.isEmpty {
            args += ["--launch-ticket", launchTicket]
        }
        return args
    }
}
