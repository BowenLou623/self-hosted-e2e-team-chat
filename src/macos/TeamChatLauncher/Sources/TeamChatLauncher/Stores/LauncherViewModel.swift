import AppKit
import Foundation

@MainActor
final class LauncherViewModel: ObservableObject {
    @Published var config: GlobalLauncherConfig
    @Published var profiles: [ProfileSummary] = []
    @Published var selectedProfile: ProfileSummary?
    @Published var launcherSettings: ProfileLauncherSettings = .fallback
    @Published var syncthingSettings: SyncthingSettings = .fallback
    @Published var syncthingStatus: SyncthingStatus?
    @Published var syncthingTestStatus: SyncthingStatus?
    @Published var syncthingTestMessage = ""
    @Published var isSyncthingTesting = false
    @Published var aiSettings: AISettings = .fallback
    @Published var aiAPIKeyInput = ""
    @Published var aiTestResult = ""
    @Published var aiSummary = ""
    @Published var aiErrorMessage = ""
    @Published var aiIncludeFileSnippets = false
    @Published var aiFileSearchQuery = ""
    @Published var aiFileSearchExtension = ""
    @Published var aiSelectedGroupID = ""
    @Published var aiFileSearchResults: [ProjectFileSearchResult] = []
    @Published var aiSelectedFile: ProjectFileSearchResult?
    @Published var lastAIFileSummary: LastAIFileSummary?
    @Published var aiLibraryStatus: AILibraryStatus = .empty
    @Published var aiLibrarySearchQuery = ""
    @Published var aiLibrarySearchResults: [AIRAGSource] = []
    @Published var aiDocumentSources: [AIDocumentSource] = []
    @Published var aiSelectedDocumentSourceID = ""
    @Published var aiConversations: [AIConversationSummary] = []
    @Published var aiSelectedConversationID = ""
    @Published var aiChatMessages: [AIConversationMessage] = []
    @Published var aiQuestion = ""
    @Published var aiAnswerSources: [AIRAGSource] = []
    @Published var isAIWorking = false
    @Published var aiSettingsDirty = false
    @Published var syncItems: [SyncOverview] = []
    @Published var environmentReport: EnvironmentCheckReport?
    @Published var bootstrapResult: EnvironmentBootstrapResult = .empty
    @Published var isBootstrapping = false
    @Published var onboardingDismissedForSession = false
    @Published var projectIndexStatus: ProjectIndexStatus = .empty
    @Published var fileSearchQuery = ""
    @Published var fileSearchExtension = ""
    @Published var fileSearchResults: [ProjectFileSearchResult] = []
    @Published var isIndexing = false
    @Published var isSearchingFiles = false
    @Published var isUnbindingSync = false
    @Published var statusMessage = ""
    @Published var pythonStatus: PythonInterpreterStatus
    @Published var hubProbeMessage = ""
    @Published var discoveredHubs: [HubDiscoveryResult] = []
    @Published var isDiscoveringHubs = false
    @Published var deviceSummary: DeviceSummary = .empty
    @Published var deviceNameDraft = ""
    @Published var hubAdminTokenInput = ""
    @Published var hubAdminStatus: HubAdminStatus = .empty
    @Published var hubAdminRegisterPassword = ""
    @Published var hubAdminRegisterConfirm = ""
    @Published var hubAdminLoginPassword = ""
    @Published var hubAdminAuthMessage = ""
    @Published var hubDestroyConfirm = ""
    @Published var hubDestroyIncludeLogs = false
    @Published var hubDestroyResult: HubAdminDestroyResult?
    @Published var isResolvingHubForLaunch = false
    @Published var launchTicket = ""
    @Published var createProfileName = ""
    @Published var createDisplayName = ""
    @Published var createPassword = ""
    @Published var createConfirmPassword = ""
    @Published var loginPassword = ""
    @Published var syncthingAPIKeyInput = ""
    @Published var securityStatus: SecurityStatusReport = .empty

    let processController = ChatProcessController()

    private let configStore = GlobalConfigStore()
    private let controlClient = ControlCLIClient()
    private lazy var bootstrapService = EnvironmentBootstrapService(controlClient: controlClient)

    init() {
        let loadedConfig = configStore.load()
        self.config = loadedConfig
        self.pythonStatus = .unknown(executable: loadedConfig.pythonExecutable)
    }

    var shouldShowOnboarding: Bool {
        guard !onboardingDismissedForSession else { return false }
        if !config.hasCompletedOnboarding {
            return true
        }
        return environmentReport?.status == "error"
    }

    var activeProfileName: String {
        selectedProfile?.profile ?? (config.selectedProfile.isEmpty ? "alice" : config.selectedProfile)
    }

    func loadInitialState() {
        Task {
            await validatePythonInterpreter()
            await refreshProfiles()
            await checkEnvironment()
            await verifyEnvironmentBootstrap()
            await loadManualEnvironmentCommands()
        }
    }

    func saveGlobalConfig() {
        do {
            if config.venvPath.isEmpty, !config.projectRoot.isEmpty {
                config.venvPath = URL(fileURLWithPath: config.projectRoot).appendingPathComponent(".venv").path
            }
            try configStore.save(config)
            statusMessage = "全局配置已保存"
            Task {
                await validatePythonInterpreter()
                await checkEnvironment()
                await verifyEnvironmentBootstrap()
            }
        } catch {
            statusMessage = error.localizedDescription
        }
    }

    func validatePythonInterpreter() async {
        guard !config.pythonExecutable.isEmpty else {
            pythonStatus = .unknown(executable: "")
            statusMessage = "请先配置 Python 可执行文件"
            return
        }
        pythonStatus = await PythonInterpreterResolver.validate(
            executable: config.pythonExecutable,
            projectRoot: config.projectRoot
        )
        if !pythonStatus.controlOK {
            statusMessage = pythonStatus.summary
        }
    }

    func refreshProfiles() async {
        do {
            profiles = try await controlClient.listProfiles(config: config)
            if let selected = profiles.first(where: { $0.profile == config.selectedProfile }) {
                await selectProfile(selected)
            } else if selectedProfile == nil, let first = profiles.first {
                await selectProfile(first)
            } else if profiles.isEmpty {
                selectedProfile = nil
            }
        } catch {
            statusMessage = error.localizedDescription
        }
    }

    func selectProfile(_ profile: ProfileSummary) async {
        selectedProfile = profile
        config.selectedProfile = profile.profile
        try? configStore.save(config)
        loginPassword = ""
        launchTicket = ""
        await reloadSelectedProfileState()
        await verifyEnvironmentBootstrap()
        await loadManualEnvironmentCommands()
    }

    func reloadSelectedProfileState() async {
        guard let selectedProfile else { return }
        do {
            launcherSettings = try await controlClient.loadLauncherSettings(profile: selectedProfile.profile, config: config)
            syncthingSettings = try await controlClient.loadSyncthingSettings(profile: selectedProfile.profile, config: config)
            syncthingStatus = try await controlClient.detectSyncthing(profile: selectedProfile.profile, config: config)
            syncthingTestStatus = syncthingStatus
            aiSettings = try await controlClient.loadAISettings(profile: selectedProfile.profile, config: config)
            aiSettingsDirty = false
            syncItems = try await controlClient.listSync(profile: selectedProfile.profile, config: config)
            projectIndexStatus = try await controlClient.projectIndexStatus(profile: selectedProfile.profile, config: config)
            if aiSelectedGroupID.isEmpty, let first = syncItems.first {
                aiSelectedGroupID = first.groupId
            }
            aiLibraryStatus = try await controlClient.aiLibraryStatus(profile: selectedProfile.profile, groupID: aiSelectedGroupID, config: config)
            if aiSelectedGroupID.isEmpty {
                aiDocumentSources = []
            } else {
                aiDocumentSources = try await controlClient.listAILibrarySources(
                    profile: selectedProfile.profile,
                    groupID: aiSelectedGroupID,
                    query: aiLibrarySearchQuery,
                    config: config
                ).sources
            }
            aiConversations = try await controlClient.listAIConversations(profile: selectedProfile.profile, groupID: aiSelectedGroupID, config: config)
            securityStatus = try await controlClient.securityStatus(profile: selectedProfile.profile, config: config)
            deviceSummary = try await controlClient.deviceSummary(profile: selectedProfile.profile, config: config)
            deviceNameDraft = deviceSummary.deviceName
            hubAdminStatus = try await controlClient.hubAdminStatus(token: hubAdminTokenInput, config: config)
        } catch {
            statusMessage = error.localizedDescription
        }
    }

    func createProfileAndLogin() async {
        let profile = createProfileName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !profile.isEmpty else {
            statusMessage = "Profile 名称不能为空"
            return
        }
        guard createPassword == createConfirmPassword else {
            statusMessage = "两次输入的密码不一致"
            return
        }
        do {
            _ = try await controlClient.createProfile(profile, config: config)
            let result = try await controlClient.authInit(
                profile: profile,
                displayName: createDisplayName,
                password: createPassword,
                config: config
            )
            launchTicket = result.launchTicket
            createProfileName = ""
            createDisplayName = ""
            createPassword = ""
            createConfirmPassword = ""
            profiles = try await controlClient.listProfiles(config: config)
            if let created = profiles.first(where: { $0.profile == result.profile.profile }) {
                await selectProfile(created)
                launchTicket = result.launchTicket
            }
            statusMessage = "Profile 已创建并登录"
        } catch {
            statusMessage = error.localizedDescription
        }
    }

    func loginSelectedProfile() async {
        guard let selectedProfile else { return }
        do {
            let result = try await controlClient.authLogin(
                profile: selectedProfile.profile,
                password: loginPassword,
                config: config
            )
            launchTicket = result.launchTicket
            loginPassword = ""
            profiles = try await controlClient.listProfiles(config: config)
            if let refreshed = profiles.first(where: { $0.profile == selectedProfile.profile }) {
                self.selectedProfile = refreshed
            }
            statusMessage = "登录成功，可以启动客户端"
        } catch {
            launchTicket = ""
            statusMessage = error.localizedDescription
        }
    }

    func saveLauncherSettings() async {
        guard let selectedProfile else { return }
        do {
            launcherSettings = try await controlClient.saveLauncherSettings(
                profile: selectedProfile.profile,
                settings: launcherSettings,
                config: config
            )
            statusMessage = "Hub 配置已保存"
        } catch {
            statusMessage = error.localizedDescription
        }
    }

    func probeHub() async {
        let result = await HubProbe.probe(address: launcherSettings.hubAddress)
        hubProbeMessage = result.message
    }

    func discoverHubs() async {
        isDiscoveringHubs = true
        defer { isDiscoveringHubs = false }
        do {
            discoveredHubs = try await controlClient.discoverHubs(config: config)
            statusMessage = discoveredHubs.isEmpty ? "未发现局域网 Hub" : "发现 \(discoveredHubs.count) 个 Hub"
        } catch {
            statusMessage = error.localizedDescription
        }
    }

    func useDiscoveredHub(_ hub: HubDiscoveryResult) async {
        guard let reachableAddress = await reachableAddress(for: hub) else {
            statusMessage = "发现 Hub，但 TCP 无法连接：\(hub.address)。请确认主机防火墙允许 Hub 端口。"
            hubProbeMessage = statusMessage
            return
        }
        launcherSettings.transport = "network"
        launcherSettings.hubAddress = reachableAddress
        await saveLauncherSettings()
        await probeHub()
    }

    func refreshDeviceSummary() async {
        guard let selectedProfile else { return }
        do {
            deviceSummary = try await controlClient.deviceSummary(profile: selectedProfile.profile, config: config)
            deviceNameDraft = deviceSummary.deviceName
        } catch {
            statusMessage = error.localizedDescription
        }
    }

    func saveDeviceName() async {
        guard let selectedProfile else { return }
        do {
            deviceSummary = try await controlClient.saveDeviceName(
                profile: selectedProfile.profile,
                deviceName: deviceNameDraft,
                config: config
            )
            deviceNameDraft = deviceSummary.deviceName
            profiles = try await controlClient.listProfiles(config: config)
            statusMessage = "设备名称已保存"
        } catch {
            statusMessage = error.localizedDescription
        }
    }

    func refreshHubAdminStatus() async {
        do {
            hubAdminStatus = try await controlClient.hubAdminStatus(token: hubAdminTokenInput, config: config)
            if !hubAdminStatus.authenticated && !hubAdminTokenInput.isEmpty {
                hubAdminAuthMessage = "Admin token 未验证"
            }
        } catch {
            statusMessage = error.localizedDescription
        }
    }

    func registerHubAdmin() async {
        guard hubAdminStatus.localHubRunning else {
            hubAdminAuthMessage = hubAdminStatus.deniedReason.isEmpty ? "Admin 只能在 Hub 主机本机注册" : hubAdminStatus.deniedReason
            return
        }
        guard !hubAdminRegisterPassword.isEmpty else {
            hubAdminAuthMessage = "请输入 admin 密码"
            return
        }
        guard hubAdminRegisterPassword == hubAdminRegisterConfirm else {
            hubAdminAuthMessage = "两次密码不一致"
            return
        }
        do {
            let auth = try await controlClient.hubAdminInit(password: hubAdminRegisterPassword, config: config)
            hubAdminTokenInput = auth.token
            hubAdminRegisterPassword = ""
            hubAdminRegisterConfirm = ""
            hubAdminLoginPassword = ""
            hubAdminAuthMessage = "admin 已注册并登录"
            await refreshHubAdminStatus()
        } catch {
            hubAdminAuthMessage = error.localizedDescription
            statusMessage = error.localizedDescription
        }
    }

    func loginHubAdmin() async {
        guard hubAdminStatus.localHubRunning else {
            hubAdminAuthMessage = hubAdminStatus.deniedReason.isEmpty ? "Admin 只能在 Hub 主机本机登录" : hubAdminStatus.deniedReason
            return
        }
        guard !hubAdminLoginPassword.isEmpty else {
            hubAdminAuthMessage = "请输入 admin 密码"
            return
        }
        do {
            let auth = try await controlClient.hubAdminLogin(password: hubAdminLoginPassword, config: config)
            hubAdminTokenInput = auth.token
            hubAdminLoginPassword = ""
            hubAdminAuthMessage = auth.authenticated ? "admin 已登录" : "admin 登录失败"
            await refreshHubAdminStatus()
        } catch {
            hubAdminAuthMessage = error.localizedDescription
            statusMessage = error.localizedDescription
        }
    }

    func dryRunDestroyHub() async {
        do {
            hubDestroyResult = try await controlClient.hubAdminDestroy(
                token: hubAdminTokenInput,
                confirm: hubDestroyConfirm,
                execute: false,
                includeLogs: hubDestroyIncludeLogs,
                config: config
            )
            statusMessage = "Hub 销毁 dry-run 完成"
            await refreshHubAdminStatus()
        } catch {
            hubDestroyResult = nil
            statusMessage = error.localizedDescription
        }
    }

    func executeDestroyHub() async {
        do {
            hubDestroyResult = try await controlClient.hubAdminDestroy(
                token: hubAdminTokenInput,
                confirm: hubDestroyConfirm,
                execute: true,
                includeLogs: hubDestroyIncludeLogs,
                config: config
            )
            statusMessage = "Hub 本地内容已销毁"
            hubAdminTokenInput = ""
            hubDestroyConfirm = ""
            hubAdminAuthMessage = "Hub 本地内容已销毁，admin 需要重新注册"
            await refreshHubAdminStatus()
        } catch {
            statusMessage = error.localizedDescription
        }
    }

    func saveSyncthingSettings() async {
        guard let selectedProfile else { return }
        do {
            syncthingSettings = try await controlClient.saveSyncthingSettings(
                profile: selectedProfile.profile,
                baseURL: syncthingSettings.baseUrl,
                apiKey: syncthingAPIKeyInput,
                timeoutSeconds: syncthingSettings.timeoutSeconds,
                config: config
            )
            syncthingAPIKeyInput = ""
            syncthingStatus = try await controlClient.detectSyncthing(profile: selectedProfile.profile, config: config)
            syncthingTestStatus = syncthingStatus
            securityStatus = try await controlClient.securityStatus(profile: selectedProfile.profile, config: config)
            statusMessage = "Syncthing 配置已保存"
        } catch {
            statusMessage = error.localizedDescription
        }
    }

    func detectSyncthing() async {
        guard let selectedProfile else { return }
        do {
            syncthingStatus = try await controlClient.detectSyncthing(profile: selectedProfile.profile, config: config)
            syncthingTestStatus = syncthingStatus
            securityStatus = try await controlClient.securityStatus(profile: selectedProfile.profile, config: config)
        } catch {
            statusMessage = error.localizedDescription
        }
    }

    func testSyncthingConnection() async {
        guard let selectedProfile else { return }
        isSyncthingTesting = true
        defer { isSyncthingTesting = false }
        do {
            let status = try await controlClient.testSyncthing(
                profile: selectedProfile.profile,
                baseURL: syncthingSettings.baseUrl.isEmpty ? SyncthingSettings.fallback.baseUrl : syncthingSettings.baseUrl,
                apiKey: syncthingAPIKeyInput,
                timeoutSeconds: syncthingSettings.timeoutSeconds,
                config: config
            )
            syncthingTestStatus = status
            syncthingStatus = status
            syncthingTestMessage = status?.error?.isEmpty == false ? status?.error ?? "" : status?.label ?? "未检测"
            statusMessage = "Syncthing 测试: \(status?.label ?? "未检测")"
            securityStatus = try await controlClient.securityStatus(profile: selectedProfile.profile, config: config)
        } catch {
            syncthingTestMessage = error.localizedDescription
            statusMessage = error.localizedDescription
        }
    }

    func copySyncthingDeviceID() {
        guard let deviceID = syncthingStatus?.deviceId, !deviceID.isEmpty else {
            statusMessage = "当前没有可复制的 Device ID"
            return
        }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(deviceID, forType: .string)
        statusMessage = "Device ID 已复制"
    }

    func saveAISettings() async {
        guard let selectedProfile else { return }
        do {
            aiSettings = try await controlClient.saveAISettings(
                profile: selectedProfile.profile,
                settings: aiSettings,
                apiKey: aiAPIKeyInput,
                config: config
            )
            aiAPIKeyInput = ""
            aiSettingsDirty = false
            securityStatus = try await controlClient.securityStatus(profile: selectedProfile.profile, config: config)
            statusMessage = "AI 配置已保存"
        } catch {
            statusMessage = error.localizedDescription
        }
    }

    func markAISettingsDirty() {
        aiSettingsDirty = true
    }

    func applyAIProviderDefaultsIfNeeded() {
        switch aiSettings.providerType {
        case "ollama":
            if aiSettings.baseUrl.isEmpty || aiSettings.baseUrl == "http://127.0.0.1:1234/v1" {
                aiSettings.baseUrl = "http://127.0.0.1:11434"
            }
        case "lm_studio":
            if aiSettings.baseUrl.isEmpty || aiSettings.baseUrl == "http://127.0.0.1:11434" {
                aiSettings.baseUrl = "http://127.0.0.1:1234/v1"
            }
        default:
            break
        }
        aiSettingsDirty = true
    }

    private func saveAISettingsBeforeActionIfNeeded() async throws {
        guard let selectedProfile else { return }
        aiSettings = try await controlClient.saveAISettings(
            profile: selectedProfile.profile,
            settings: aiSettings,
            apiKey: aiAPIKeyInput,
            config: config
        )
        aiAPIKeyInput = ""
        aiSettingsDirty = false
    }

    func testAIConnection() async {
        guard let selectedProfile else { return }
        isAIWorking = true
        defer { isAIWorking = false }
        do {
            try await saveAISettingsBeforeActionIfNeeded()
            aiTestResult = aiSettings.providerType == "lm_studio" && aiSettings.autoLoadLocalModel
                ? "正在准备 LM Studio：启动 server / 加载模型 / 测试连接..."
                : "正在测试连接..."
            let result = try await controlClient.testAI(profile: selectedProfile.profile, config: config)
            aiTestResult = result.reply
            aiErrorMessage = ""
            securityStatus = try await controlClient.securityStatus(profile: selectedProfile.profile, config: config)
            statusMessage = "AI 连接测试成功"
        } catch {
            aiTestResult = error.localizedDescription
            aiErrorMessage = error.localizedDescription
            statusMessage = error.localizedDescription
        }
    }

    func summarizeAIProject() async {
        guard let selectedProfile else { return }
        isAIWorking = true
        defer { isAIWorking = false }
        do {
            try await saveAISettingsBeforeActionIfNeeded()
            aiSummary = aiSettings.providerType == "lm_studio" && aiSettings.autoLoadLocalModel
                ? "正在准备 LM Studio：启动 server / 加载模型 / 请求项目说明..."
                : "正在请求项目说明..."
            let result = try await controlClient.summarizeAIProject(
                profile: selectedProfile.profile,
                groupID: aiSelectedGroupID,
                includeFileSnippets: aiIncludeFileSnippets,
                fileID: aiIncludeFileSnippets ? (aiSelectedFile?.id ?? "") : "",
                config: config
            )
            aiSummary = result.summary
            aiErrorMessage = ""
            securityStatus = try await controlClient.securityStatus(profile: selectedProfile.profile, config: config)
        } catch {
            aiSummary = error.localizedDescription
            aiErrorMessage = error.localizedDescription
            statusMessage = error.localizedDescription
        }
    }

    func searchAIFiles() async {
        guard let selectedProfile else { return }
        isAIWorking = true
        defer { isAIWorking = false }
        do {
            try await saveAISettingsBeforeActionIfNeeded()
            aiFileSearchResults = try await controlClient.searchAIFiles(
                profile: selectedProfile.profile,
                query: aiFileSearchQuery,
                groupID: aiSelectedGroupID,
                fileExtension: aiFileSearchExtension,
                config: config
            )
        } catch {
            statusMessage = error.localizedDescription
        }
    }

    func summarizeAISelectedFile() async {
        guard let selectedProfile, let aiSelectedFile else { return }
        isAIWorking = true
        defer { isAIWorking = false }
        do {
            try await saveAISettingsBeforeActionIfNeeded()
            aiSummary = aiSettings.providerType == "lm_studio" && aiSettings.autoLoadLocalModel
                ? "正在准备 LM Studio：启动 server / 加载模型 / 请求文件摘要..."
                : "正在请求文件摘要..."
            let result = try await controlClient.summarizeAIFile(
                profile: selectedProfile.profile,
                fileID: aiSelectedFile.id,
                config: config
            )
            aiSummary = result.summary
            aiErrorMessage = ""
            lastAIFileSummary = LastAIFileSummary(
                providerLabel: aiSettings.providerLabel,
                providerType: aiSettings.providerType,
                providerLocation: aiSettings.providerLocation,
                fileName: aiSelectedFile.fileName,
                relativePath: aiSelectedFile.relativePath,
                summarizedAt: Date().timeIntervalSince1970
            )
            securityStatus = try await controlClient.securityStatus(profile: selectedProfile.profile, config: config)
        } catch {
            aiSummary = error.localizedDescription
            aiErrorMessage = error.localizedDescription
            statusMessage = error.localizedDescription
        }
    }

    func refreshAILibraryStatus() async {
        guard let selectedProfile else { return }
        guard !aiSelectedGroupID.isEmpty else {
            aiLibraryStatus = .empty
            aiDocumentSources = []
            return
        }
        do {
            aiLibraryStatus = try await controlClient.aiLibraryStatus(
                profile: selectedProfile.profile,
                groupID: aiSelectedGroupID,
                config: config
            )
        } catch {
            statusMessage = error.localizedDescription
        }
    }

    func buildAILibrary() async {
        guard let selectedProfile else { return }
        guard !aiSelectedGroupID.isEmpty else {
            statusMessage = "请选择项目"
            return
        }
        isAIWorking = true
        defer { isAIWorking = false }
        do {
            try await saveAISettingsBeforeActionIfNeeded()
            let result = try await controlClient.buildAILibrary(
                profile: selectedProfile.profile,
                groupID: aiSelectedGroupID,
                config: config
            )
            aiLibraryStatus = result.library
            await refreshAILibrarySources()
            statusMessage = "AI 文档库已更新：\(result.summary.chunkCount) 个 chunk"
            securityStatus = try await controlClient.securityStatus(profile: selectedProfile.profile, config: config)
        } catch {
            aiErrorMessage = error.localizedDescription
            statusMessage = error.localizedDescription
        }
    }

    func refreshAILibrarySources(status: String = "") async {
        guard let selectedProfile else { return }
        guard !aiSelectedGroupID.isEmpty else {
            aiDocumentSources = []
            aiSelectedDocumentSourceID = ""
            return
        }
        do {
            let result = try await controlClient.listAILibrarySources(
                profile: selectedProfile.profile,
                groupID: aiSelectedGroupID,
                status: status,
                query: aiLibrarySearchQuery,
                config: config
            )
            aiDocumentSources = result.sources
            if !aiSelectedDocumentSourceID.isEmpty,
               !aiDocumentSources.contains(where: { $0.sourceId == aiSelectedDocumentSourceID }) {
                aiSelectedDocumentSourceID = ""
            }
        } catch {
            statusMessage = error.localizedDescription
        }
    }

    func deleteAIDocumentSource(_ source: AIDocumentSource) async {
        guard let selectedProfile else { return }
        isAIWorking = true
        defer { isAIWorking = false }
        do {
            _ = try await controlClient.deleteAILibrarySource(
                profile: selectedProfile.profile,
                groupID: aiSelectedGroupID,
                sourceID: source.sourceId,
                config: config
            )
            aiSelectedDocumentSourceID = ""
            await refreshAILibraryStatus()
            await refreshAILibrarySources()
            statusMessage = "已删除本机 AI 索引：\(source.relativePath)"
        } catch {
            aiErrorMessage = error.localizedDescription
            statusMessage = error.localizedDescription
        }
    }

    func restoreAIDocumentSource(_ source: AIDocumentSource) async {
        guard let selectedProfile else { return }
        isAIWorking = true
        defer { isAIWorking = false }
        do {
            _ = try await controlClient.restoreAILibrarySource(
                profile: selectedProfile.profile,
                groupID: aiSelectedGroupID,
                sourceID: source.sourceId,
                config: config
            )
            await refreshAILibraryStatus()
            await refreshAILibrarySources()
            statusMessage = "已恢复文档记录，重新构建后生效：\(source.relativePath)"
        } catch {
            aiErrorMessage = error.localizedDescription
            statusMessage = error.localizedDescription
        }
    }

    func searchAILibrary() async {
        guard let selectedProfile else { return }
        isAIWorking = true
        defer { isAIWorking = false }
        do {
            let result = try await controlClient.searchAILibrary(
                profile: selectedProfile.profile,
                query: aiLibrarySearchQuery,
                groupID: aiSelectedGroupID,
                config: config
            )
            aiLibrarySearchResults = result.results
            await refreshAILibrarySources()
            aiErrorMessage = ""
        } catch {
            aiErrorMessage = error.localizedDescription
            statusMessage = error.localizedDescription
        }
    }

    func askAIQuestion() async {
        guard let selectedProfile else { return }
        guard !aiSelectedGroupID.isEmpty else {
            statusMessage = "请选择项目"
            return
        }
        let question = aiQuestion.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !question.isEmpty else {
            statusMessage = "请输入问题"
            return
        }
        isAIWorking = true
        defer { isAIWorking = false }
        do {
            try await saveAISettingsBeforeActionIfNeeded()
            let result = try await controlClient.askAIQuestion(
                profile: selectedProfile.profile,
                question: question,
                groupID: aiSelectedGroupID,
                conversationID: aiSelectedConversationID,
                config: config
            )
            aiSelectedConversationID = result.conversationId
            if let userMessage = result.userMessage {
                aiChatMessages.append(userMessage)
            }
            if let assistantMessage = result.assistantMessage {
                aiChatMessages.append(assistantMessage)
            }
            aiAnswerSources = result.sources
            aiQuestion = ""
            aiErrorMessage = ""
            aiConversations = try await controlClient.listAIConversations(
                profile: selectedProfile.profile,
                groupID: aiSelectedGroupID,
                config: config
            )
            securityStatus = try await controlClient.securityStatus(profile: selectedProfile.profile, config: config)
        } catch {
            aiErrorMessage = error.localizedDescription
            statusMessage = error.localizedDescription
        }
    }

    func refreshAIConversations() async {
        guard let selectedProfile else { return }
        guard !aiSelectedGroupID.isEmpty else {
            aiConversations = []
            return
        }
        do {
            aiConversations = try await controlClient.listAIConversations(
                profile: selectedProfile.profile,
                groupID: aiSelectedGroupID,
                config: config
            )
        } catch {
            statusMessage = error.localizedDescription
        }
    }

    func loadAIConversation(_ conversationID: String) async {
        guard let selectedProfile else { return }
        do {
            aiSelectedConversationID = conversationID
            aiChatMessages = try await controlClient.loadAIConversation(
                profile: selectedProfile.profile,
                conversationID: conversationID,
                config: config
            )
            aiAnswerSources = []
        } catch {
            statusMessage = error.localizedDescription
        }
    }

    func startNewAIConversation() {
        aiSelectedConversationID = ""
        aiChatMessages = []
        aiAnswerSources = []
        aiQuestion = ""
    }

    func clearAIConversation() async {
        guard let selectedProfile else { return }
        guard !aiSelectedConversationID.isEmpty else { return }
        do {
            try await controlClient.clearAIConversation(
                profile: selectedProfile.profile,
                conversationID: aiSelectedConversationID,
                config: config
            )
            aiChatMessages = []
            aiAnswerSources = []
            aiConversations = try await controlClient.listAIConversations(
                profile: selectedProfile.profile,
                groupID: aiSelectedGroupID,
                config: config
            )
        } catch {
            statusMessage = error.localizedDescription
        }
    }

    func deleteAIConversation() async {
        guard let selectedProfile else { return }
        guard !aiSelectedConversationID.isEmpty else { return }
        do {
            try await controlClient.deleteAIConversation(
                profile: selectedProfile.profile,
                conversationID: aiSelectedConversationID,
                config: config
            )
            startNewAIConversation()
            aiConversations = try await controlClient.listAIConversations(
                profile: selectedProfile.profile,
                groupID: aiSelectedGroupID,
                config: config
            )
        } catch {
            statusMessage = error.localizedDescription
        }
    }

    func refreshSecurityStatus() async {
        guard let selectedProfile else { return }
        do {
            securityStatus = try await controlClient.securityStatus(profile: selectedProfile.profile, config: config)
        } catch {
            statusMessage = error.localizedDescription
        }
    }

    func refreshSync(groupID: String? = nil) async {
        guard let selectedProfile else { return }
        do {
            if let groupID, !groupID.isEmpty {
                _ = try await controlClient.refreshSync(profile: selectedProfile.profile, groupID: groupID, config: config)
                syncItems = try await controlClient.listSync(profile: selectedProfile.profile, config: config)
            } else {
                syncItems = try await controlClient.refreshSync(profile: selectedProfile.profile, groupID: groupID, config: config)
            }
        } catch {
            statusMessage = error.localizedDescription
        }
    }

    func unbindSyncProject(_ item: SyncOverview, localOnly: Bool = false) async {
        guard let selectedProfile else { return }
        isUnbindingSync = true
        defer { isUnbindingSync = false }
        do {
            let result = try await controlClient.unbindSyncProject(
                profile: selectedProfile.profile,
                groupID: item.groupId,
                localOnly: localOnly,
                config: config
            )
            syncItems = try await controlClient.listSync(profile: selectedProfile.profile, config: config)
            projectIndexStatus = try await controlClient.projectIndexStatus(profile: selectedProfile.profile, config: config)
            fileSearchResults = []
            if aiSelectedGroupID == item.groupId {
                aiSelectedGroupID = syncItems.first?.groupId ?? ""
                startNewAIConversation()
                aiLibrarySearchResults = []
                aiDocumentSources = []
                await refreshAILibraryStatus()
                await refreshAILibrarySources()
                await refreshAIConversations()
            }
            statusMessage = result.previousSyncthingFolderId.isEmpty
                ? "已删除本机项目绑定，不会删除真实文件"
                : "已解绑本机 Syncthing folder 并删除本机项目绑定"
        } catch {
            statusMessage = error.localizedDescription
        }
    }

    func checkEnvironment() async {
        do {
            environmentReport = try await controlClient.checkEnvironment(profile: selectedProfile?.profile, config: config)
        } catch {
            statusMessage = error.localizedDescription
        }
    }

    func verifyEnvironmentBootstrap() async {
        guard !config.projectRoot.isEmpty else { return }
        do {
            bootstrapResult = try await bootstrapService.verify(profile: selectedProfile?.profile, config: config)
            if let report = bootstrapResult.report {
                environmentReport = report
            }
        } catch {
            statusMessage = error.localizedDescription
        }
    }

    func runEnvironmentBootstrap(installDeps: Bool = true, verifyClient: Bool = true) async {
        let profile = activeProfileName
        guard !profile.isEmpty else {
            statusMessage = "请选择或输入 profile"
            return
        }
        guard !config.projectRoot.isEmpty else {
            statusMessage = "请先选择 Python 项目根目录"
            return
        }
        isBootstrapping = true
        defer { isBootstrapping = false }
        do {
            bootstrapResult = try await bootstrapService.bootstrap(
                profile: profile,
                installDeps: installDeps,
                verifyClient: verifyClient,
                config: config
            )
            if let pythonExecutable = bootstrapResult.pythonExecutable, !pythonExecutable.isEmpty {
                config.pythonExecutable = pythonExecutable
            }
            if let venvPath = bootstrapResult.venvPath, !venvPath.isEmpty {
                config.venvPath = venvPath
            }
            try configStore.save(config)
            await validatePythonInterpreter()
            await checkEnvironment()
            statusMessage = bootstrapResult.status == "failed" ? "自动配置失败，请复制日志排查" : "自动配置已完成"
        } catch {
            statusMessage = error.localizedDescription
        }
    }

    func loadManualEnvironmentCommands() async {
        guard !config.projectRoot.isEmpty else { return }
        do {
            let result = try await bootstrapService.manualCommands(profile: activeProfileName, config: config)
            if bootstrapResult.copyableCommands.isEmpty {
                bootstrapResult.copyableCommands = result.copyableCommands
            }
        } catch {
            statusMessage = error.localizedDescription
        }
    }

    func setInstallMode(_ mode: String) {
        guard ["automatic", "manual"].contains(mode) else { return }
        config.installMode = mode
        try? configStore.save(config)
    }

    func finishOnboarding() {
        config.hasCompletedOnboarding = true
        onboardingDismissedForSession = true
        try? configStore.save(config)
    }

    func skipOnboarding() {
        config.hasCompletedOnboarding = true
        onboardingDismissedForSession = true
        try? configStore.save(config)
        statusMessage = "已跳过安装向导，可随时从安装工作台重新打开"
    }

    func copyBootstrapLogs() {
        let text = bootstrapResult.logs.map { entry in
            let command = entry.command.isEmpty ? "" : "\n$ \(entry.command)"
            let detail = entry.detail.isEmpty ? "" : "\n\(entry.detail)"
            return "[\(entry.level)] \(entry.message)\(command)\(detail)"
        }.joined(separator: "\n\n")
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
        statusMessage = "安装日志已复制"
    }

    func copyCommand(_ command: CopyableCommand) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(command.command, forType: .string)
        statusMessage = "命令已复制"
    }

    func openPythonDownloadPage() {
        guard let url = URL(string: "https://www.python.org/downloads/macos/") else { return }
        NSWorkspace.shared.open(url)
    }

    func copyHomebrewPythonCommand() {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString("brew install python@3.11", forType: .string)
        statusMessage = "Homebrew Python 命令已复制"
    }

    func refreshProjectIndexStatus(groupID: String? = nil) async {
        guard let selectedProfile else { return }
        do {
            projectIndexStatus = try await controlClient.projectIndexStatus(
                profile: selectedProfile.profile,
                groupID: groupID,
                config: config
            )
        } catch {
            statusMessage = error.localizedDescription
        }
    }

    func scanProjectIndex(groupID: String? = nil) async {
        guard let selectedProfile else { return }
        isIndexing = true
        defer { isIndexing = false }
        do {
            projectIndexStatus = try await controlClient.scanProjectIndex(
                profile: selectedProfile.profile,
                groupID: groupID,
                config: config
            )
            fileSearchResults = try await controlClient.searchProjectFiles(
                profile: selectedProfile.profile,
                query: fileSearchQuery,
                groupID: groupID,
                fileExtension: fileSearchExtension,
                config: config
            )
            statusMessage = "项目文件索引已更新"
        } catch {
            statusMessage = error.localizedDescription
        }
    }

    func searchProjectFiles(groupID: String? = nil) async {
        guard let selectedProfile else { return }
        isSearchingFiles = true
        defer { isSearchingFiles = false }
        do {
            fileSearchResults = try await controlClient.searchProjectFiles(
                profile: selectedProfile.profile,
                query: fileSearchQuery,
                groupID: groupID,
                fileExtension: fileSearchExtension,
                config: config
            )
        } catch {
            statusMessage = error.localizedDescription
        }
    }

    func startClient() {
        Task { await startClientResolved() }
    }

    private func startClientResolved() async {
        guard let selectedProfile else {
            statusMessage = "请选择 profile"
            return
        }
        guard selectedProfile.hasIdentity else {
            statusMessage = "请先创建或初始化 profile"
            return
        }
        guard !selectedProfile.hasPassword || !launchTicket.isEmpty else {
            statusMessage = "请先输入本地密码登录"
            return
        }
        guard pythonStatus.canLaunchClient else {
            statusMessage = pythonStatus.pySideOK ? pythonStatus.summary : "当前 Python 缺少 PySide6，不能启动聊天客户端"
            return
        }
        guard await resolveHubBeforeLaunch() else {
            return
        }
        do {
            try processController.start(
                profile: selectedProfile,
                settings: launcherSettings,
                launchTicket: launchTicket.isEmpty ? nil : launchTicket,
                config: config
            )
            launchTicket = ""
            statusMessage = "客户端启动中"
        } catch {
            statusMessage = error.localizedDescription
        }
    }

    func resolveHubBeforeLaunch() async -> Bool {
        guard launcherSettings.transport == "network" else {
            return true
        }
        isResolvingHubForLaunch = true
        defer { isResolvingHubForLaunch = false }

        let currentAddress = launcherSettings.hubAddress.trimmingCharacters(in: .whitespacesAndNewlines)
        let currentLooksLocal = isLoopbackHubAddress(currentAddress)
        let probe = await HubProbe.probe(address: currentAddress, timeout: 1.2)
        hubProbeMessage = probe.message
        if probe.reachable && !currentLooksLocal {
            return true
        }

        do {
            isDiscoveringHubs = true
            discoveredHubs = try await controlClient.discoverHubs(timeout: 2.0, config: config)
            isDiscoveringHubs = false
            if let best = await firstReachableHub(in: discoveredHubs) {
                launcherSettings.transport = "network"
                launcherSettings.hubAddress = best.reachableAddress
                await saveLauncherSettings()
                hubProbeMessage = "已自动连接 \(best.reachableAddress)"
                statusMessage = "已自动发现并使用 Hub: \(best.reachableAddress)"
                return true
            }
        } catch {
            isDiscoveringHubs = false
            if probe.reachable && !currentLooksLocal {
                statusMessage = "未发现其他 Hub，继续使用当前 Hub: \(currentAddress)"
                return true
            }
            statusMessage = error.localizedDescription
            return false
        }

        if probe.reachable && (!currentLooksLocal || hubAdminStatus.localHubRunning) {
            statusMessage = "未发现其他 Hub，继续使用当前 Hub: \(currentAddress)"
            return true
        }
        if discoveredHubs.isEmpty {
            statusMessage = "未发现可用 Hub。请确认主机已启动 Hub，或在 Hub 页手动填写主机地址。"
        } else {
            statusMessage = "发现 Hub 但 TCP 无法连接。请确认主机 Hub 监听 0.0.0.0，且防火墙允许 8080 端口。"
        }
        return false
    }

    private func firstReachableHub(in hubs: [HubDiscoveryResult]) async -> (hub: HubDiscoveryResult, reachableAddress: String)? {
        for hub in hubs.sorted(by: { ($0.responseMs ?? Int.max) < ($1.responseMs ?? Int.max) }) {
            if let address = await reachableAddress(for: hub) {
                return (hub, address)
            }
        }
        return nil
    }

    private func reachableAddress(for hub: HubDiscoveryResult) async -> String? {
        for address in candidateAddresses(for: hub) {
            let probe = await HubProbe.probe(address: address, timeout: 1.2)
            if probe.reachable {
                return address
            }
        }
        return nil
    }

    private func candidateAddresses(for hub: HubDiscoveryResult) -> [String] {
        var candidates: [String] = []
        func add(_ address: String?) {
            let value = (address ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            guard HubAddressValidator.isValid(value), !candidates.contains(value) else { return }
            candidates.append(value)
        }
        add(hub.address)
        add(hub.sourceAddress)
        if let sourceHost = hub.sourceHost, !sourceHost.isEmpty, hub.port > 0 {
            add("\(sourceHost):\(hub.port)")
        }
        if let payloadHost = hub.payloadHost, !payloadHost.isEmpty, hub.port > 0 {
            add("\(payloadHost):\(hub.port)")
        }
        return candidates
    }

    private func isLoopbackHubAddress(_ address: String) -> Bool {
        guard let parts = HubAddressValidator.split(address) else {
            return true
        }
        let host = parts.host.lowercased()
        return ["127.0.0.1", "localhost", "::1", "0.0.0.0"].contains(host)
    }

    func stopClient() {
        processController.stop()
    }

    func openProjectFolder(_ path: String) {
        guard !path.isEmpty else { return }
        NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)])
    }

    func revealIndexedFile(_ result: ProjectFileSearchResult) {
        guard !result.absolutePath.isEmpty else { return }
        NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: result.absolutePath)])
    }

    func openIndexedFile(_ result: ProjectFileSearchResult) {
        guard !result.absolutePath.isEmpty else { return }
        NSWorkspace.shared.open(URL(fileURLWithPath: result.absolutePath))
    }

    func revealAISource(_ source: AIRAGSource) {
        guard !source.absolutePath.isEmpty else { return }
        NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: source.absolutePath)])
    }

    func openAISource(_ source: AIRAGSource) {
        guard !source.absolutePath.isEmpty else { return }
        NSWorkspace.shared.open(URL(fileURLWithPath: source.absolutePath))
    }

    func copyAISourcePath(_ source: AIRAGSource) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(source.absolutePath.isEmpty ? source.relativePath : source.absolutePath, forType: .string)
    }

    func copyAISourceCitation(_ source: AIRAGSource) {
        let label = source.displaySourceIndex.isEmpty ? "S" : source.displaySourceIndex
        let citation = "[\(label)] \(source.relativePath):\(source.lineStart)-\(source.lineEnd)"
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(citation, forType: .string)
    }

    func openAIDocumentSource(_ source: AIDocumentSource) {
        guard !source.absolutePath.isEmpty else { return }
        NSWorkspace.shared.open(URL(fileURLWithPath: source.absolutePath))
    }

    func revealAIDocumentSource(_ source: AIDocumentSource) {
        guard !source.absolutePath.isEmpty else { return }
        NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: source.absolutePath)])
    }

    func copyAIDocumentSourcePath(_ source: AIDocumentSource) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(source.absolutePath.isEmpty ? source.relativePath : source.absolutePath, forType: .string)
    }

    func openSyncthingWebUI() {
        guard let url = URL(string: syncthingSettings.baseUrl) else { return }
        NSWorkspace.shared.open(url)
    }
}
