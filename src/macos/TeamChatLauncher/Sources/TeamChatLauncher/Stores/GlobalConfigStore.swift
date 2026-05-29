import Foundation

final class GlobalConfigStore {
    private let fileManager = FileManager.default

    var configURL: URL {
        let base = fileManager.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSTemporaryDirectory())
        return base
            .appendingPathComponent("InstantMessagingTeamLauncher", isDirectory: true)
            .appendingPathComponent("config.json")
    }

    func load() -> GlobalLauncherConfig {
        let url = configURL
        guard let data = try? Data(contentsOf: url) else {
            return .default()
        }
        let decoder = JSONDecoder()
        var config = (try? decoder.decode(GlobalLauncherConfig.self, from: data)) ?? .default()
        var migrated = false
        if config.pythonExecutable.isEmpty || config.pythonExecutable == "python3" {
            config.pythonExecutable = PythonInterpreterResolver.preferredExecutable()
            migrated = true
        }
        if config.projectRoot.isEmpty, let detectedRoot = ProjectRootResolver.detectProjectRoot() {
            config.projectRoot = detectedRoot
            migrated = true
        }
        if config.venvPath.isEmpty, !config.projectRoot.isEmpty {
            config.venvPath = URL(fileURLWithPath: config.projectRoot).appendingPathComponent(".venv").path
            migrated = true
        }
        if !["automatic", "manual"].contains(config.installMode) {
            config.installMode = "automatic"
            migrated = true
        }
        if migrated {
            try? save(config)
        }
        return config
    }

    func save(_ config: GlobalLauncherConfig) throws {
        let url = configURL
        try fileManager.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        let data = try encoder.encode(config)
        try data.write(to: url, options: [.atomic])
    }
}
