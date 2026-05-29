import Foundation

enum ControlCLIError: LocalizedError {
    case missingProjectRoot
    case processFailed(Int32, String)
    case commandError(String)
    case decodeFailed(String)

    var errorDescription: String? {
        switch self {
        case .missingProjectRoot:
            return "请先配置 Python 项目路径。"
        case .processFailed(let code, let stderr):
            return "control CLI 退出码 \(code): \(PythonInterpreterResolver.compactOutput(stderr))"
        case .commandError(let message):
            return message
        case .decodeFailed(let output):
            return "无法解析 control CLI 输出: \(output)"
        }
    }
}

final class ControlCLIClient {
    private let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }()

    func listProfiles(config: GlobalLauncherConfig) async throws -> [ProfileSummary] {
        let data = try await run(arguments: ["profile", "list"], config: config)
        return try decode(ProfilesEnvelope.self, from: data).profiles ?? []
    }

    func inspectProfile(_ profile: String, config: GlobalLauncherConfig) async throws -> ProfileSummary {
        let data = try await run(arguments: ["profile", "inspect", "--profile", profile], config: config)
        guard let profile = try decode(ProfileEnvelope.self, from: data).profile else {
            throw ControlCLIError.decodeFailed(String(data: data, encoding: .utf8) ?? "")
        }
        return profile
    }

    func createProfile(_ profile: String, config: GlobalLauncherConfig) async throws -> ProfileSummary {
        let data = try await run(arguments: ["profile", "create", "--profile", profile], config: config)
        guard let profile = try decode(ProfileEnvelope.self, from: data).profile else {
            throw ControlCLIError.decodeFailed(String(data: data, encoding: .utf8) ?? "")
        }
        return profile
    }

    func loadLauncherSettings(profile: String, config: GlobalLauncherConfig) async throws -> ProfileLauncherSettings {
        let data = try await run(arguments: ["launcher", "get", "--profile", profile], config: config)
        return try decode(LauncherSettingsEnvelope.self, from: data).settings ?? .fallback
    }

    func saveLauncherSettings(
        profile: String,
        settings: ProfileLauncherSettings,
        config: GlobalLauncherConfig
    ) async throws -> ProfileLauncherSettings {
        let data = try await run(
            arguments: [
                "launcher", "save",
                "--profile", profile,
                "--transport", settings.transport,
                "--hub-address", settings.hubAddress,
                "--log-level", settings.logLevel
            ],
            config: config
        )
        return try decode(LauncherSettingsEnvelope.self, from: data).settings ?? settings
    }

    func authInit(
        profile: String,
        displayName: String,
        password: String,
        config: GlobalLauncherConfig
    ) async throws -> AuthResult {
        let data = try await run(
            arguments: ["auth", "init", "--profile", profile, "--display-name", displayName],
            stdin: password + "\n",
            config: config
        )
        return try decode(AuthEnvelope.self, from: data).asResult()
    }

    func authLogin(profile: String, password: String, config: GlobalLauncherConfig) async throws -> AuthResult {
        let data = try await run(
            arguments: ["auth", "login", "--profile", profile],
            stdin: password + "\n",
            config: config
        )
        return try decode(AuthEnvelope.self, from: data).asResult()
    }

    func loadSyncthingSettings(profile: String, config: GlobalLauncherConfig) async throws -> SyncthingSettings {
        let data = try await run(arguments: ["syncthing", "get", "--profile", profile], config: config)
        return try decode(SyncthingSettingsEnvelope.self, from: data).settings ?? .fallback
    }

    func saveSyncthingSettings(
        profile: String,
        baseURL: String,
        apiKey: String,
        timeoutSeconds: Double,
        config: GlobalLauncherConfig
    ) async throws -> SyncthingSettings {
        var args = [
            "syncthing", "save",
            "--profile", profile,
            "--base-url", baseURL,
            "--timeout-seconds", String(timeoutSeconds)
        ]
        if !apiKey.isEmpty {
            args += ["--api-key", apiKey]
        }
        let data = try await run(arguments: args, config: config)
        return try decode(SyncthingSettingsEnvelope.self, from: data).settings ?? .fallback
    }

    func detectSyncthing(profile: String, config: GlobalLauncherConfig) async throws -> SyncthingStatus? {
        let data = try await run(arguments: ["syncthing", "detect", "--profile", profile], config: config)
        return try decode(SyncthingStatusEnvelope.self, from: data).status
    }

    func testSyncthing(
        profile: String,
        baseURL: String,
        apiKey: String,
        timeoutSeconds: Double,
        config: GlobalLauncherConfig
    ) async throws -> SyncthingStatus? {
        var args = [
            "syncthing", "test",
            "--profile", profile,
            "--base-url", baseURL,
            "--timeout-seconds", String(timeoutSeconds)
        ]
        if !apiKey.isEmpty {
            args += ["--api-key", apiKey]
        }
        let data = try await run(arguments: args, config: config)
        return try decode(SyncthingStatusEnvelope.self, from: data).status
    }

    func listSync(profile: String, config: GlobalLauncherConfig) async throws -> [SyncOverview] {
        let data = try await run(arguments: ["sync", "list", "--profile", profile], config: config)
        return try decode(SyncEnvelope.self, from: data).items ?? []
    }

    func refreshSync(profile: String, groupID: String? = nil, config: GlobalLauncherConfig) async throws -> [SyncOverview] {
        var args = ["sync", "refresh", "--profile", profile]
        if let groupID, !groupID.isEmpty {
            args += ["--group-id", groupID]
        }
        let data = try await run(arguments: args, config: config)
        return try decode(SyncEnvelope.self, from: data).items ?? []
    }

    func unbindSyncProject(
        profile: String,
        groupID: String,
        localOnly: Bool = false,
        config: GlobalLauncherConfig
    ) async throws -> SyncUnbindResult {
        var args = ["sync", "unbind", "--profile", profile, "--group-id", groupID]
        if localOnly {
            args += ["--local-only"]
        }
        let data = try await run(arguments: args, config: config)
        guard let result = try decode(SyncUnbindEnvelope.self, from: data).result else {
            throw ControlCLIError.decodeFailed(String(data: data, encoding: .utf8) ?? "")
        }
        return result
    }

    func checkEnvironment(profile: String?, config: GlobalLauncherConfig) async throws -> EnvironmentCheckReport {
        var args = ["environment", "check"]
        if let profile, !profile.isEmpty {
            args += ["--profile", profile]
        }
        let data = try await run(arguments: args, config: config)
        guard let report = try decode(EnvironmentEnvelope.self, from: data).report else {
            throw ControlCLIError.decodeFailed(String(data: data, encoding: .utf8) ?? "")
        }
        return report
    }

    func verifyEnvironment(profile: String?, venvPath: String, config: GlobalLauncherConfig) async throws -> EnvironmentBootstrapResult {
        var args = ["environment", "verify"]
        if let profile, !profile.isEmpty {
            args += ["--profile", profile]
        }
        if !venvPath.isEmpty {
            args += ["--venv-path", venvPath]
        }
        let data = try await run(arguments: args, config: config)
        return try decode(EnvironmentBootstrapEnvelope.self, from: data).asResult()
    }

    func bootstrapEnvironment(
        profile: String,
        venvPath: String,
        installDeps: Bool,
        verifyClient: Bool,
        config: GlobalLauncherConfig
    ) async throws -> EnvironmentBootstrapResult {
        var args = [
            "environment", "bootstrap",
            "--profile", profile
        ]
        if !venvPath.isEmpty {
            args += ["--venv-path", venvPath]
        }
        if installDeps {
            args += ["--install-deps"]
        }
        if verifyClient {
            args += ["--verify-client"]
        }
        let data = try await run(arguments: args, config: config)
        return try decode(EnvironmentBootstrapEnvelope.self, from: data).asResult()
    }

    func manualEnvironmentCommands(profile: String, venvPath: String, config: GlobalLauncherConfig) async throws -> EnvironmentBootstrapResult {
        var args = [
            "environment", "manual-commands",
            "--profile", profile
        ]
        if !venvPath.isEmpty {
            args += ["--venv-path", venvPath]
        }
        let data = try await run(arguments: args, config: config)
        return try decode(EnvironmentBootstrapEnvelope.self, from: data).asResult()
    }

    func projectIndexStatus(profile: String, groupID: String? = nil, config: GlobalLauncherConfig) async throws -> ProjectIndexStatus {
        var args = ["index", "status", "--profile", profile]
        if let groupID, !groupID.isEmpty {
            args += ["--group-id", groupID]
        }
        let data = try await run(arguments: args, config: config)
        return try decode(ProjectIndexStatusEnvelope.self, from: data).status ?? .empty
    }

    func scanProjectIndex(profile: String, groupID: String? = nil, config: GlobalLauncherConfig) async throws -> ProjectIndexStatus {
        var args = ["index", "scan", "--profile", profile]
        if let groupID, !groupID.isEmpty {
            args += ["--group-id", groupID]
        }
        let data = try await run(arguments: args, config: config)
        return try decode(ProjectIndexScanEnvelope.self, from: data).status ?? .empty
    }

    func searchProjectFiles(
        profile: String,
        query: String,
        groupID: String? = nil,
        fileExtension: String = "",
        limit: Int = 50,
        config: GlobalLauncherConfig
    ) async throws -> [ProjectFileSearchResult] {
        var args = [
            "index", "search",
            "--profile", profile,
            "--query", query,
            "--limit", String(limit)
        ]
        if let groupID, !groupID.isEmpty {
            args += ["--group-id", groupID]
        }
        if !fileExtension.isEmpty {
            args += ["--extension", fileExtension]
        }
        let data = try await run(arguments: args, config: config)
        return try decode(ProjectFileSearchEnvelope.self, from: data).results ?? []
    }

    func locateProjectFile(profile: String, fileID: String, config: GlobalLauncherConfig) async throws -> ProjectFileSearchResult {
        let data = try await run(arguments: ["index", "locate", "--profile", profile, "--file-id", fileID], config: config)
        guard let file = try decode(ProjectFileLocateEnvelope.self, from: data).file else {
            throw ControlCLIError.decodeFailed(String(data: data, encoding: .utf8) ?? "")
        }
        return file
    }

    func loadAISettings(profile: String, config: GlobalLauncherConfig) async throws -> AISettings {
        let data = try await run(arguments: ["ai", "settings", "get", "--profile", profile], config: config)
        return try decode(AISettingsEnvelope.self, from: data).settings ?? .fallback
    }

    func saveAISettings(profile: String, settings: AISettings, apiKey: String, config: GlobalLauncherConfig) async throws -> AISettings {
        var args = [
            "ai", "settings", "save",
            "--profile", profile,
            "--provider-type", settings.providerType,
            "--base-url", settings.baseUrl,
            "--model", settings.model,
            "--timeout-seconds", String(settings.timeoutSeconds),
            "--max-file-bytes", String(settings.maxFileBytes),
            "--max-document-bytes", String(settings.maxDocumentBytes),
            "--auto-load-local-model", settings.autoLoadLocalModel ? "true" : "false",
            "--lmstudio-model-key", settings.lmstudioModelKey,
            "--lms-path", settings.lmsPath,
            "--rag-max-context-chars", String(settings.ragMaxContextChars),
            "--rag-max-chunks", String(settings.ragMaxChunks),
            "--conversation-recent-turns", String(settings.conversationRecentTurns),
            "--embedding-enabled", settings.embeddingEnabled ? "true" : "false",
            "--embedding-model", settings.embeddingModel
        ]
        if !apiKey.isEmpty {
            args += ["--api-key", apiKey]
        }
        let data = try await run(arguments: args, config: config)
        return try decode(AISettingsEnvelope.self, from: data).settings ?? settings
    }

    func testAI(profile: String, config: GlobalLauncherConfig) async throws -> AITestResult {
        let data = try await run(arguments: ["ai", "test", "--profile", profile], config: config)
        guard let result = try decode(AITestEnvelope.self, from: data).result else {
            throw ControlCLIError.decodeFailed(String(data: data, encoding: .utf8) ?? "")
        }
        return result
    }

    func summarizeAIProject(
        profile: String,
        groupID: String,
        projectID: String = "",
        includeFileSnippets: Bool = false,
        fileID: String = "",
        config: GlobalLauncherConfig
    ) async throws -> AIProjectSummaryResult {
        var args = ["ai", "project-summary", "--profile", profile]
        if !groupID.isEmpty {
            args += ["--group-id", groupID]
        }
        if !projectID.isEmpty {
            args += ["--project-id", projectID]
        }
        if includeFileSnippets {
            args += ["--include-file-snippets"]
        }
        if !fileID.isEmpty {
            args += ["--file-id", fileID]
        }
        let data = try await run(arguments: args, config: config)
        guard let result = try decode(AIProjectSummaryEnvelope.self, from: data).result else {
            throw ControlCLIError.decodeFailed(String(data: data, encoding: .utf8) ?? "")
        }
        return result
    }

    func searchAIFiles(profile: String, query: String, groupID: String, fileExtension: String, config: GlobalLauncherConfig) async throws -> [ProjectFileSearchResult] {
        var args = [
            "ai", "search-files",
            "--profile", profile,
            "--query", query,
            "--limit", "30"
        ]
        if !groupID.isEmpty {
            args += ["--group-id", groupID]
        }
        if !fileExtension.isEmpty {
            args += ["--extension", fileExtension]
        }
        let data = try await run(arguments: args, config: config)
        return try decode(ProjectFileSearchEnvelope.self, from: data).results ?? []
    }

    func summarizeAIFile(profile: String, fileID: String, config: GlobalLauncherConfig) async throws -> AIFileSummaryResult {
        let data = try await run(arguments: ["ai", "file-summary", "--profile", profile, "--file-id", fileID], config: config)
        guard let result = try decode(AIFileSummaryEnvelope.self, from: data).result else {
            throw ControlCLIError.decodeFailed(String(data: data, encoding: .utf8) ?? "")
        }
        return result
    }

    func aiLibraryStatus(profile: String, groupID: String, projectID: String = "", config: GlobalLauncherConfig) async throws -> AILibraryStatus {
        var args = ["ai", "library", "status", "--profile", profile]
        if !groupID.isEmpty {
            args += ["--group-id", groupID]
        }
        if !projectID.isEmpty {
            args += ["--project-id", projectID]
        }
        let data = try await run(arguments: args, config: config)
        return try decode(AILibraryStatusEnvelope.self, from: data).status ?? .empty
    }

    func buildAILibrary(profile: String, groupID: String, projectID: String = "", config: GlobalLauncherConfig) async throws -> AILibraryBuildResult {
        var args = ["ai", "library", "build", "--profile", profile, "--group-id", groupID]
        if !projectID.isEmpty {
            args += ["--project-id", projectID]
        }
        let data = try await run(arguments: args, config: config)
        guard let result = try decode(AILibraryBuildEnvelope.self, from: data).result else {
            throw ControlCLIError.decodeFailed(String(data: data, encoding: .utf8) ?? "")
        }
        return result
    }

    func searchAILibrary(profile: String, query: String, groupID: String, projectID: String = "", limit: Int = 20, config: GlobalLauncherConfig) async throws -> AILibrarySearchResult {
        var args = ["ai", "library", "search", "--profile", profile, "--limit", String(limit)]
        if !groupID.isEmpty {
            args += ["--group-id", groupID]
        }
        if !projectID.isEmpty {
            args += ["--project-id", projectID]
        }
        let data = try await run(arguments: args, stdin: jsonInput(["query": query]), config: config)
        guard let result = try decode(AILibrarySearchEnvelope.self, from: data).result else {
            throw ControlCLIError.decodeFailed(String(data: data, encoding: .utf8) ?? "")
        }
        return result
    }

    func listAILibrarySources(
        profile: String,
        groupID: String,
        projectID: String = "",
        status: String = "",
        query: String = "",
        limit: Int = 100,
        config: GlobalLauncherConfig
    ) async throws -> AILibrarySourceListResult {
        var args = [
            "ai", "library", "list",
            "--profile", profile,
            "--limit", String(limit)
        ]
        if !groupID.isEmpty {
            args += ["--group-id", groupID]
        }
        if !projectID.isEmpty {
            args += ["--project-id", projectID]
        }
        if !status.isEmpty {
            args += ["--status", status]
        }
        if !query.isEmpty {
            args += ["--query", query]
        }
        let data = try await run(arguments: args, config: config)
        guard let result = try decode(AILibrarySourceListEnvelope.self, from: data).result else {
            throw ControlCLIError.decodeFailed(String(data: data, encoding: .utf8) ?? "")
        }
        return result
    }

    func deleteAILibrarySource(
        profile: String,
        groupID: String,
        sourceID: String,
        fileID: String = "",
        projectID: String = "",
        config: GlobalLauncherConfig
    ) async throws -> AILibrarySourceMutationResult {
        var args = ["ai", "library", "delete", "--profile", profile]
        if !groupID.isEmpty {
            args += ["--group-id", groupID]
        }
        if !projectID.isEmpty {
            args += ["--project-id", projectID]
        }
        if !sourceID.isEmpty {
            args += ["--source-id", sourceID]
        }
        if !fileID.isEmpty {
            args += ["--file-id", fileID]
        }
        let data = try await run(arguments: args, config: config)
        guard let result = try decode(AILibrarySourceMutationEnvelope.self, from: data).result else {
            throw ControlCLIError.decodeFailed(String(data: data, encoding: .utf8) ?? "")
        }
        return result
    }

    func restoreAILibrarySource(
        profile: String,
        groupID: String,
        sourceID: String,
        fileID: String = "",
        projectID: String = "",
        config: GlobalLauncherConfig
    ) async throws -> AILibrarySourceMutationResult {
        var args = ["ai", "library", "restore", "--profile", profile]
        if !groupID.isEmpty {
            args += ["--group-id", groupID]
        }
        if !projectID.isEmpty {
            args += ["--project-id", projectID]
        }
        if !sourceID.isEmpty {
            args += ["--source-id", sourceID]
        }
        if !fileID.isEmpty {
            args += ["--file-id", fileID]
        }
        let data = try await run(arguments: args, config: config)
        guard let result = try decode(AILibrarySourceMutationEnvelope.self, from: data).result else {
            throw ControlCLIError.decodeFailed(String(data: data, encoding: .utf8) ?? "")
        }
        return result
    }

    func askAIQuestion(profile: String, question: String, groupID: String, projectID: String = "", conversationID: String = "", config: GlobalLauncherConfig) async throws -> AIRAGAnswerResult {
        var args = ["ai", "ask", "--profile", profile, "--group-id", groupID]
        if !projectID.isEmpty {
            args += ["--project-id", projectID]
        }
        if !conversationID.isEmpty {
            args += ["--conversation-id", conversationID]
        }
        let data = try await run(arguments: args, stdin: jsonInput(["question": question]), config: config)
        guard let result = try decode(AIRAGAnswerEnvelope.self, from: data).result else {
            throw ControlCLIError.decodeFailed(String(data: data, encoding: .utf8) ?? "")
        }
        return result
    }

    func listAIConversations(profile: String, groupID: String, projectID: String = "", config: GlobalLauncherConfig) async throws -> [AIConversationSummary] {
        var args = ["ai", "conversations", "list", "--profile", profile]
        if !groupID.isEmpty {
            args += ["--group-id", groupID]
        }
        if !projectID.isEmpty {
            args += ["--project-id", projectID]
        }
        let data = try await run(arguments: args, config: config)
        return try decode(AIConversationsEnvelope.self, from: data).conversations ?? []
    }

    func loadAIConversation(profile: String, conversationID: String, config: GlobalLauncherConfig) async throws -> [AIConversationMessage] {
        let data = try await run(arguments: ["ai", "conversations", "show", "--profile", profile, "--conversation-id", conversationID], config: config)
        return try decode(AIConversationDetailEnvelope.self, from: data).messages ?? []
    }

    func clearAIConversation(profile: String, conversationID: String, config: GlobalLauncherConfig) async throws {
        _ = try await run(arguments: ["ai", "conversations", "clear", "--profile", profile, "--conversation-id", conversationID], config: config)
    }

    func deleteAIConversation(profile: String, conversationID: String, config: GlobalLauncherConfig) async throws {
        _ = try await run(arguments: ["ai", "conversations", "delete", "--profile", profile, "--conversation-id", conversationID], config: config)
    }

    func securityStatus(profile: String, config: GlobalLauncherConfig) async throws -> SecurityStatusReport {
        let data = try await run(arguments: ["security", "status", "--profile", profile], config: config)
        return try decode(SecurityStatusEnvelope.self, from: data).report ?? .empty
    }

    func deviceSummary(profile: String, config: GlobalLauncherConfig) async throws -> DeviceSummary {
        let data = try await run(arguments: ["device", "get", "--profile", profile], config: config)
        return try decode(DeviceEnvelope.self, from: data).device ?? .empty
    }

    func saveDeviceName(profile: String, deviceName: String, config: GlobalLauncherConfig) async throws -> DeviceSummary {
        let data = try await run(
            arguments: ["device", "save", "--profile", profile, "--device-name", deviceName],
            config: config
        )
        return try decode(DeviceEnvelope.self, from: data).device ?? .empty
    }

    func discoverHubs(timeout: Double = 2.0, broadcastAddresses: [String] = [], config: GlobalLauncherConfig) async throws -> [HubDiscoveryResult] {
        var args = ["hub", "discover", "--timeout", String(timeout)]
        for address in broadcastAddresses where !address.isEmpty {
            args += ["--broadcast-address", address]
        }
        let data = try await run(arguments: args, config: config)
        return try decode(HubDiscoveryEnvelope.self, from: data).hubs ?? []
    }

    func hubAdminInit(password: String, config: GlobalLauncherConfig) async throws -> HubAdminAuthResult {
        let data = try await run(
            arguments: ["hub", "admin-init", "--password-stdin"],
            stdin: password,
            config: config
        )
        return try decode(HubAdminAuthEnvelope.self, from: data).auth ?? .empty
    }

    func hubAdminLogin(password: String, config: GlobalLauncherConfig) async throws -> HubAdminAuthResult {
        let data = try await run(
            arguments: ["hub", "admin-login", "--password-stdin"],
            stdin: password,
            config: config
        )
        return try decode(HubAdminAuthEnvelope.self, from: data).auth ?? .empty
    }

    func hubAdminStatus(token: String, config: GlobalLauncherConfig) async throws -> HubAdminStatus {
        var args = ["hub", "admin-status"]
        if !token.isEmpty {
            args += ["--token", token]
        }
        let data = try await run(arguments: args, config: config)
        return try decode(HubAdminStatusEnvelope.self, from: data).admin ?? .empty
    }

    func hubAdminDestroy(
        token: String,
        confirm: String,
        execute: Bool,
        includeLogs: Bool,
        config: GlobalLauncherConfig
    ) async throws -> HubAdminDestroyResult {
        var args = ["hub", "admin-destroy", "--token", token, "--confirm", confirm]
        if execute {
            args += ["--execute"]
        }
        if includeLogs {
            args += ["--include-logs"]
        }
        let data = try await run(arguments: args, config: config)
        guard let result = try decode(HubAdminDestroyEnvelope.self, from: data).result else {
            throw ControlCLIError.decodeFailed(String(data: data, encoding: .utf8) ?? "")
        }
        return result
    }

    private func run(arguments: [String], stdin: String? = nil, config: GlobalLauncherConfig) async throws -> Data {
        guard !config.projectRoot.isEmpty else {
            throw ControlCLIError.missingProjectRoot
        }

        return try await Task.detached(priority: .userInitiated) {
            let pythonArgs = ["-m", "src.app.control", "--project-root", config.projectRoot] + arguments
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
            let stdinPipe = Pipe()
            process.standardOutput = stdout
            process.standardError = stderr
            process.standardInput = stdinPipe

            try process.run()
            if let stdin {
                stdinPipe.fileHandleForWriting.write(Data(stdin.utf8))
            }
            try? stdinPipe.fileHandleForWriting.close()
            process.waitUntilExit()

            let stdoutData = stdout.fileHandleForReading.readDataToEndOfFile()
            let stderrText = String(data: stderr.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
            if process.terminationStatus != 0 {
                if let envelope = try? self.decoder.decode(BaseEnvelope.self, from: stdoutData), let error = envelope.error {
                    throw ControlCLIError.commandError(PythonInterpreterResolver.compactOutput(error))
                }
                throw ControlCLIError.processFailed(process.terminationStatus, stderrText)
            }
            return stdoutData
        }.value
    }

    private func decode<T: Decodable>(_ type: T.Type, from data: Data) throws -> T {
        let base = try decoder.decode(BaseEnvelope.self, from: data)
        if base.ok == false {
            throw ControlCLIError.commandError(base.error ?? "control CLI command failed")
        }
        return try decoder.decode(T.self, from: data)
    }

    private func jsonInput(_ payload: [String: String]) throws -> String {
        let data = try JSONSerialization.data(withJSONObject: payload, options: [])
        return String(data: data, encoding: .utf8) ?? "{}"
    }
}

struct BaseEnvelope: Decodable {
    let ok: Bool?
    let error: String?
}

struct ProfilesEnvelope: Decodable {
    let ok: Bool
    let profiles: [ProfileSummary]?
}

struct ProfileEnvelope: Decodable {
    let ok: Bool
    let profile: ProfileSummary?
}

struct LauncherSettingsEnvelope: Decodable {
    let ok: Bool
    let settings: ProfileLauncherSettings?
}

struct AuthResult {
    let profile: ProfileSummary
    let launchTicket: String
    let expiresAt: Double
}

struct AuthEnvelope: Decodable {
    let ok: Bool
    let profile: ProfileSummary?
    let launchTicket: String?
    let expiresAt: Double?

    func asResult() throws -> AuthResult {
        guard let profile, let launchTicket, let expiresAt else {
            throw ControlCLIError.decodeFailed("missing auth result fields")
        }
        return AuthResult(profile: profile, launchTicket: launchTicket, expiresAt: expiresAt)
    }
}

struct SyncthingSettingsEnvelope: Decodable {
    let ok: Bool
    let settings: SyncthingSettings?
}

struct SyncthingStatusEnvelope: Decodable {
    let ok: Bool
    let status: SyncthingStatus?
}

struct SyncEnvelope: Decodable {
    let ok: Bool
    let items: [SyncOverview]?
}

struct SyncUnbindEnvelope: Decodable {
    let ok: Bool
    let result: SyncUnbindResult?
}

struct EnvironmentEnvelope: Decodable {
    let ok: Bool
    let report: EnvironmentCheckReport?
}

struct EnvironmentBootstrapEnvelope: Decodable {
    let ok: Bool
    let status: String?
    let steps: [BootstrapStep]?
    let logs: [BootstrapLogEntry]?
    let nextActions: [String]?
    let copyableCommands: [CopyableCommand]?
    let venvPath: String?
    let pythonExecutable: String?
    let plannedCommands: [String]?
    let report: EnvironmentCheckReport?

    func asResult() throws -> EnvironmentBootstrapResult {
        EnvironmentBootstrapResult(
            status: status ?? "needs_action",
            steps: steps ?? [],
            logs: logs ?? [],
            nextActions: nextActions ?? [],
            copyableCommands: copyableCommands ?? [],
            venvPath: venvPath,
            pythonExecutable: pythonExecutable,
            plannedCommands: plannedCommands,
            report: report
        )
    }
}

struct ProjectIndexStatusEnvelope: Decodable {
    let ok: Bool
    let status: ProjectIndexStatus?
}

struct ProjectIndexScanEnvelope: Decodable {
    let ok: Bool
    let status: ProjectIndexStatus?
}

struct ProjectFileSearchEnvelope: Decodable {
    let ok: Bool
    let results: [ProjectFileSearchResult]?
}

struct ProjectFileLocateEnvelope: Decodable {
    let ok: Bool
    let file: ProjectFileSearchResult?
}

struct AISettingsEnvelope: Decodable {
    let ok: Bool
    let settings: AISettings?
}

struct AITestEnvelope: Decodable {
    let ok: Bool
    let result: AITestResult?
}

struct AIProjectSummaryEnvelope: Decodable {
    let ok: Bool
    let result: AIProjectSummaryResult?
}

struct AIFileSummaryEnvelope: Decodable {
    let ok: Bool
    let result: AIFileSummaryResult?
}

struct AILibraryStatusEnvelope: Decodable {
    let ok: Bool
    let status: AILibraryStatus?
}

struct AILibraryBuildEnvelope: Decodable {
    let ok: Bool
    let result: AILibraryBuildResult?
}

struct AILibrarySearchEnvelope: Decodable {
    let ok: Bool
    let result: AILibrarySearchResult?
}

struct AILibrarySourceListEnvelope: Decodable {
    let ok: Bool
    let result: AILibrarySourceListResult?
}

struct AILibrarySourceMutationEnvelope: Decodable {
    let ok: Bool
    let result: AILibrarySourceMutationResult?
}

struct AIRAGAnswerEnvelope: Decodable {
    let ok: Bool
    let result: AIRAGAnswerResult?
}

struct AIConversationsEnvelope: Decodable {
    let ok: Bool
    let conversations: [AIConversationSummary]?
}

struct AIConversationDetailEnvelope: Decodable {
    let ok: Bool
    let messages: [AIConversationMessage]?
}

struct SecurityStatusEnvelope: Decodable {
    let ok: Bool
    let report: SecurityStatusReport?
}

struct DeviceEnvelope: Decodable {
    let ok: Bool
    let device: DeviceSummary?
}

struct HubDiscoveryEnvelope: Decodable {
    let ok: Bool
    let hubs: [HubDiscoveryResult]?
}

struct HubAdminStatusEnvelope: Decodable {
    let ok: Bool
    let admin: HubAdminStatus?
}

struct HubAdminAuthEnvelope: Decodable {
    let ok: Bool
    let auth: HubAdminAuthResult?
}

struct HubAdminDestroyEnvelope: Decodable {
    let ok: Bool
    let result: HubAdminDestroyResult?
}
