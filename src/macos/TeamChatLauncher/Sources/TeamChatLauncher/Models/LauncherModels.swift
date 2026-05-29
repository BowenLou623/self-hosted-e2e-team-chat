import Foundation

enum SidebarItem: String, CaseIterable, Identifiable {
    case overview
    case profiles
    case install
    case environment
    case hub
    case devices
    case admin
    case syncthing
    case sync
    case security
    case logs
    case ai

    var id: String { rawValue }

    var title: String {
        switch self {
        case .overview: return "概览"
        case .profiles: return "Profiles"
        case .install: return "安装工作台"
        case .environment: return "环境检查"
        case .hub: return "Hub"
        case .devices: return "设备"
        case .admin: return "Admin"
        case .syncthing: return "Syncthing"
        case .sync: return "项目同步"
        case .security: return "安全状态"
        case .logs: return "日志"
        case .ai: return "AI 项目助手"
        }
    }

    var systemImage: String {
        switch self {
        case .overview: return "speedometer"
        case .profiles: return "person.crop.circle"
        case .install: return "shippingbox.and.arrow.backward"
        case .environment: return "checklist"
        case .hub: return "network"
        case .devices: return "desktopcomputer"
        case .admin: return "exclamationmark.shield"
        case .syncthing: return "arrow.triangle.2.circlepath"
        case .sync: return "folder.badge.gearshape"
        case .security: return "lock.shield"
        case .logs: return "doc.text.magnifyingglass"
        case .ai: return "sparkles"
        }
    }
}

enum ChatProcessState: String {
    case notConfigured
    case stopped
    case launching
    case running
    case stopping
    case exited
    case failed

    var label: String {
        switch self {
        case .notConfigured: return "未配置"
        case .stopped: return "已停止"
        case .launching: return "启动中"
        case .running: return "运行中"
        case .stopping: return "停止中"
        case .exited: return "已退出"
        case .failed: return "启动失败"
        }
    }
}

struct GlobalLauncherConfig: Codable, Equatable {
    var projectRoot: String
    var pythonExecutable: String
    var selectedProfile: String
    var venvPath: String
    var hasCompletedOnboarding: Bool
    var installMode: String

    static func `default`() -> GlobalLauncherConfig {
        let root = ProjectRootResolver.detectProjectRoot() ?? ""
        return GlobalLauncherConfig(
            projectRoot: root,
            pythonExecutable: PythonInterpreterResolver.preferredExecutable(),
            selectedProfile: "",
            venvPath: root.isEmpty ? "" : URL(fileURLWithPath: root).appendingPathComponent(".venv").path,
            hasCompletedOnboarding: false,
            installMode: "automatic"
        )
    }

    enum CodingKeys: String, CodingKey {
        case projectRoot
        case pythonExecutable
        case selectedProfile
        case venvPath
        case hasCompletedOnboarding
        case installMode
    }

    init(
        projectRoot: String,
        pythonExecutable: String,
        selectedProfile: String,
        venvPath: String,
        hasCompletedOnboarding: Bool,
        installMode: String
    ) {
        self.projectRoot = projectRoot
        self.pythonExecutable = pythonExecutable
        self.selectedProfile = selectedProfile
        self.venvPath = venvPath
        self.hasCompletedOnboarding = hasCompletedOnboarding
        self.installMode = installMode
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        projectRoot = try container.decodeIfPresent(String.self, forKey: .projectRoot) ?? ProjectRootResolver.detectProjectRoot() ?? ""
        pythonExecutable = try container.decodeIfPresent(String.self, forKey: .pythonExecutable) ?? PythonInterpreterResolver.preferredExecutable()
        selectedProfile = try container.decodeIfPresent(String.self, forKey: .selectedProfile) ?? ""
        venvPath = try container.decodeIfPresent(String.self, forKey: .venvPath) ?? ""
        hasCompletedOnboarding = try container.decodeIfPresent(Bool.self, forKey: .hasCompletedOnboarding) ?? false
        installMode = try container.decodeIfPresent(String.self, forKey: .installMode) ?? "automatic"
    }
}

struct PythonInterpreterStatus: Equatable {
    var executable: String
    var version: String
    var controlOK: Bool
    var pySideOK: Bool
    var summary: String
    var details: String

    static func unknown(executable: String) -> PythonInterpreterStatus {
        PythonInterpreterStatus(
            executable: executable,
            version: "",
            controlOK: false,
            pySideOK: false,
            summary: "未检测",
            details: ""
        )
    }

    var canLaunchClient: Bool {
        controlOK && pySideOK
    }
}

struct ProfileSummary: Codable, Identifiable, Equatable {
    let profile: String
    let exists: Bool
    let dataDir: String
    let dbPath: String
    let configDir: String
    let hasIdentity: Bool
    let userId: String
    let displayName: String
    let hasPassword: Bool
    let deviceId: String?
    let deviceName: String?
    let deviceFingerprint: String?

    var id: String { profile }
    var displayTitle: String { displayName.isEmpty ? profile : displayName }
}

struct ProfileLauncherSettings: Codable, Equatable {
    var transport: String
    var hubAddress: String
    var logLevel: String

    static let fallback = ProfileLauncherSettings(
        transport: "network",
        hubAddress: "127.0.0.1:8080",
        logLevel: "INFO"
    )
}

struct DeviceSummary: Codable, Equatable {
    var deviceId: String
    var deviceName: String
    var deviceFingerprint: String
    var devicePublicKey: String
    var configPath: String

    static let empty = DeviceSummary(
        deviceId: "",
        deviceName: "",
        deviceFingerprint: "",
        devicePublicKey: "",
        configPath: ""
    )
}

struct HubDiscoveryResult: Codable, Identifiable, Equatable {
    var hubId: String
    var hubName: String
    var host: String
    var port: Int
    var tempFilePort: Int
    var version: String
    var startedAt: Double
    var discoveryPort: Int?
    var address: String
    var responseMs: Int?
    var payloadHost: String?
    var sourceHost: String?
    var sourceAddress: String?

    var id: String { hubId.isEmpty ? address : hubId }
}

struct HubAdminStatus: Codable, Equatable {
    var authenticated: Bool
    var destroyPhrase: String?
    var localHubRunning: Bool
    var adminAvailable: Bool
    var adminUsername: String
    var deniedReason: String
    var hubRuntime: HubRuntimeInfo?
    var status: HubAdminStatusBody
    var devices: [HubAdminDevice]?

    static let empty = HubAdminStatus(
        authenticated: false,
        destroyPhrase: "DESTROY HUB",
        localHubRunning: false,
        adminAvailable: false,
        adminUsername: "admin",
        deniedReason: "",
        hubRuntime: nil,
        status: .empty,
        devices: nil
    )
}

struct HubRuntimeInfo: Codable, Equatable {
    var hubId: String?
    var pid: Int?
    var hostname: String?
    var machineId: String?
    var host: String?
    var port: Int?
    var tempFilePort: Int?
    var discoveryPort: Int?
    var startedAt: Double?
    var updatedAt: Double?
    var status: String?
}

struct HubAdminAuthResult: Codable, Equatable {
    var authenticated: Bool
    var initialized: Bool
    var adminUsername: String
    var token: String

    static let empty = HubAdminAuthResult(
        authenticated: false,
        initialized: false,
        adminUsername: "admin",
        token: ""
    )
}

struct HubAdminStatusBody: Codable, Equatable {
    var hubDir: String
    var dbPath: String
    var deviceCount: Int
    var offlineQueueCount: Int
    var eventCount: Int
    var adminInitialized: Bool
    var tempFileDir: String

    static let empty = HubAdminStatusBody(
        hubDir: "",
        dbPath: "",
        deviceCount: 0,
        offlineQueueCount: 0,
        eventCount: 0,
        adminInitialized: false,
        tempFileDir: ""
    )
}

struct HubAdminDevice: Codable, Identifiable, Equatable {
    var userId: String
    var deviceId: String
    var deviceName: String?
    var deviceFingerprint: String?
    var firstSeen: Double?
    var lastSeen: Double?
    var trustStatus: String?

    var id: String { "\(userId):\(deviceId)" }
}

struct HubAdminDestroyResult: Codable, Equatable {
    var dryRun: Bool
    var deleted: Bool
    var targets: [String]
}

struct SyncthingSettings: Codable, Equatable {
    var baseUrl: String
    var apiKey: String
    var timeoutSeconds: Double

    static let fallback = SyncthingSettings(
        baseUrl: "http://127.0.0.1:8384",
        apiKey: "",
        timeoutSeconds: 2.0
    )
}

struct SyncthingStatus: Codable, Equatable {
    var state: String
    var baseUrl: String?
    var deviceId: String?
    var version: String?
    var installed: Bool?
    var error: String?
    var errorCode: String?
    var repairHint: String?
    var canCopyDeviceId: Bool?

    var label: String {
        switch state {
        case "connected": return "已连接"
        case "api_unconfigured": return "API 未配置"
        case "api_key_error": return "API Key 错误"
        case "csrf_error": return "HTTP 403 / CSRF 错误"
        case "not_running": return "Syncthing 未启动"
        case "connection_failed": return "连接失败"
        case "installed_not_running": return "Syncthing 未启动"
        case "not_installed": return "未安装"
        default: return state
        }
    }
}

struct AISettings: Codable, Equatable {
    var providerType: String
    var baseUrl: String
    var model: String
    var apiKey: String
    var timeoutSeconds: Double
    var maxFileBytes: Int
    var maxDocumentBytes: Int
    var autoLoadLocalModel: Bool
    var lmstudioModelKey: String
    var lmsPath: String
    var ragMaxContextChars: Int
    var ragMaxChunks: Int
    var conversationRecentTurns: Int
    var embeddingEnabled: Bool
    var embeddingModel: String

    static let fallback = AISettings(
        providerType: "",
        baseUrl: "",
        model: "",
        apiKey: "",
        timeoutSeconds: 20,
        maxFileBytes: 200 * 1024,
        maxDocumentBytes: 1024 * 1024,
        autoLoadLocalModel: true,
        lmstudioModelKey: "",
        lmsPath: "",
        ragMaxContextChars: 12000,
        ragMaxChunks: 8,
        conversationRecentTurns: 6,
        embeddingEnabled: false,
        embeddingModel: ""
    )

    init(
        providerType: String,
        baseUrl: String,
        model: String,
        apiKey: String,
        timeoutSeconds: Double,
        maxFileBytes: Int,
        maxDocumentBytes: Int = 1024 * 1024,
        autoLoadLocalModel: Bool = true,
        lmstudioModelKey: String = "",
        lmsPath: String = "",
        ragMaxContextChars: Int = 12000,
        ragMaxChunks: Int = 8,
        conversationRecentTurns: Int = 6,
        embeddingEnabled: Bool = false,
        embeddingModel: String = ""
    ) {
        self.providerType = providerType
        self.baseUrl = baseUrl
        self.model = model
        self.apiKey = apiKey
        self.timeoutSeconds = timeoutSeconds
        self.maxFileBytes = maxFileBytes
        self.maxDocumentBytes = maxDocumentBytes
        self.autoLoadLocalModel = autoLoadLocalModel
        self.lmstudioModelKey = lmstudioModelKey
        self.lmsPath = lmsPath
        self.ragMaxContextChars = ragMaxContextChars
        self.ragMaxChunks = ragMaxChunks
        self.conversationRecentTurns = conversationRecentTurns
        self.embeddingEnabled = embeddingEnabled
        self.embeddingModel = embeddingModel
    }

    enum CodingKeys: String, CodingKey {
        case providerType
        case baseUrl
        case model
        case apiKey
        case timeoutSeconds
        case maxFileBytes
        case maxDocumentBytes
        case autoLoadLocalModel
        case lmstudioModelKey
        case lmsPath
        case ragMaxContextChars
        case ragMaxChunks
        case conversationRecentTurns
        case embeddingEnabled
        case embeddingModel
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        providerType = try container.decodeIfPresent(String.self, forKey: .providerType) ?? ""
        baseUrl = try container.decodeIfPresent(String.self, forKey: .baseUrl) ?? ""
        model = try container.decodeIfPresent(String.self, forKey: .model) ?? ""
        apiKey = try container.decodeIfPresent(String.self, forKey: .apiKey) ?? ""
        timeoutSeconds = try container.decodeIfPresent(Double.self, forKey: .timeoutSeconds) ?? 20
        maxFileBytes = try container.decodeIfPresent(Int.self, forKey: .maxFileBytes) ?? 200 * 1024
        maxDocumentBytes = try container.decodeIfPresent(Int.self, forKey: .maxDocumentBytes) ?? 1024 * 1024
        autoLoadLocalModel = try container.decodeIfPresent(Bool.self, forKey: .autoLoadLocalModel) ?? true
        lmstudioModelKey = try container.decodeIfPresent(String.self, forKey: .lmstudioModelKey) ?? ""
        lmsPath = try container.decodeIfPresent(String.self, forKey: .lmsPath) ?? ""
        ragMaxContextChars = try container.decodeIfPresent(Int.self, forKey: .ragMaxContextChars) ?? 12000
        ragMaxChunks = try container.decodeIfPresent(Int.self, forKey: .ragMaxChunks) ?? 8
        conversationRecentTurns = try container.decodeIfPresent(Int.self, forKey: .conversationRecentTurns) ?? 6
        embeddingEnabled = try container.decodeIfPresent(Bool.self, forKey: .embeddingEnabled) ?? false
        embeddingModel = try container.decodeIfPresent(String.self, forKey: .embeddingModel) ?? ""
    }

    var providerLabel: String {
        switch providerType {
        case "ollama": return "Ollama"
        case "lm_studio": return "LM Studio"
        case "openai_compatible": return "OpenAI-compatible"
        default: return "未选择"
        }
    }

    var providerLocation: String {
        if providerType == "ollama" || providerType == "lm_studio" {
            return "本地"
        }
        guard let host = URL(string: baseUrl)?.host?.lowercased(), !host.isEmpty else {
            return "未知"
        }
        if ["127.0.0.1", "localhost", "::1", "0.0.0.0"].contains(host) {
            return "本地"
        }
        return "远程"
    }
}

struct LastAIFileSummary: Codable, Equatable {
    var providerLabel: String
    var providerType: String
    var providerLocation: String
    var fileName: String
    var relativePath: String
    var summarizedAt: Double
}

struct SecurityStatusReport: Codable, Equatable {
    var checkedAt: Double
    var profile: String
    var encryption: SecurityEncryptionStatus
    var tempFiles: SecurityTempFileStatus
    var syncthing: SyncthingStatus?
    var ai: SecurityAIStatus

    static let empty = SecurityStatusReport(
        checkedAt: 0,
        profile: "",
        encryption: SecurityEncryptionStatus.empty,
        tempFiles: SecurityTempFileStatus.empty,
        syncthing: nil,
        ai: SecurityAIStatus.empty
    )
}

struct SecurityEncryptionStatus: Codable, Equatable {
    var currentMode: String
    var directEncryptedV2: Bool
    var groupEncryptedV1: Bool
    var replayProtection: Bool

    static let empty = SecurityEncryptionStatus(
        currentMode: "未检测",
        directEncryptedV2: false,
        groupEncryptedV1: false,
        replayProtection: false
    )
}

struct SecurityTempFileStatus: Codable, Equatable {
    var status: String
    var label: String
    var url: String
    var message: String
    var ttlSeconds: Int
    var maxBytes: Int
    var fileCount: Int

    static let empty = SecurityTempFileStatus(
        status: "unknown",
        label: "未检测",
        url: "",
        message: "",
        ttlSeconds: 0,
        maxBytes: 0,
        fileCount: 0
    )
}

struct SecurityAIStatus: Codable, Equatable {
    var status: String
    var providerType: String
    var providerLabel: String
    var providerLocation: String
    var baseUrl: String
    var model: String
    var configured: Bool
    var hasApiKey: Bool
    var autoLoadLocalModel: Bool?
    var lmstudioModelKey: String?
    var lmsPath: String?
    var documentLibrary: AILibraryStatus?
    var ragMaxContextChars: Int?
    var ragMaxChunks: Int?
    var embeddingEnabled: Bool?
    var embeddingModel: String?

    static let empty = SecurityAIStatus(
        status: "unknown",
        providerType: "",
        providerLabel: "未选择",
        providerLocation: "unknown",
        baseUrl: "",
        model: "",
        configured: false,
        hasApiKey: false,
        autoLoadLocalModel: nil,
        lmstudioModelKey: nil,
        lmsPath: nil,
        documentLibrary: nil,
        ragMaxContextChars: nil,
        ragMaxChunks: nil,
        embeddingEnabled: nil,
        embeddingModel: nil
    )
}

struct AITestResult: Codable, Equatable {
    var status: String
    var reply: String
}

struct AIProjectSummaryResult: Codable, Equatable {
    var summary: String
    var context: AIProjectContext?
}

struct AIProjectContext: Codable, Equatable {
    var groupId: String
    var projectId: String
    var fileCount: Int
    var totalSize: Int
    var extensionCounts: [String: Int]
    var recentFiles: [AIRecentFile]
}

struct AIRecentFile: Codable, Equatable, Identifiable {
    var projectName: String
    var groupName: String
    var relativePath: String
    var `extension`: String
    var size: Int
    var updatedAt: Double

    var id: String { "\(projectName)-\(relativePath)-\(updatedAt)" }
}

struct AIFileSummaryResult: Codable, Equatable {
    var summary: String
    var file: AIFileSummaryMetadata
    var content: AIFileSummaryContent?
}

struct AIFileSummaryMetadata: Codable, Equatable {
    var id: String
    var projectId: String
    var groupId: String
    var projectName: String
    var groupName: String
    var relativePath: String
    var fileName: String
    var `extension`: String
    var size: Int
    var mimeType: String
    var sha256: String
    var updatedAt: Double
}

struct AIFileSummaryContent: Codable, Equatable {
    var bytesRead: Int
    var truncated: Bool
    var maxFileBytes: Int
}

struct AILibraryStatus: Codable, Equatable {
    var groupId: String?
    var projectId: String?
    var candidateCount: Int
    var sourceCount: Int
    var indexedSourceCount: Int
    var chunkCount: Int
    var pendingCount: Int
    var staleCount: Int
    var missingCount: Int
    var skippedCount: Int
    var errorCount: Int
    var deletedLocalCount: Int?
    var totalSize: Int
    var lastUpdatedAt: Double
    var sourceStatusCounts: [String: Int]
    var embeddingStatus: String
    var tablesReady: Bool

    static let empty = AILibraryStatus(
        groupId: "",
        projectId: "",
        candidateCount: 0,
        sourceCount: 0,
        indexedSourceCount: 0,
        chunkCount: 0,
        pendingCount: 0,
        staleCount: 0,
        missingCount: 0,
        skippedCount: 0,
        errorCount: 0,
        deletedLocalCount: 0,
        totalSize: 0,
        lastUpdatedAt: 0,
        sourceStatusCounts: [:],
        embeddingStatus: "reserved_disabled",
        tablesReady: false
    )
}

struct AILibraryBuildResult: Codable, Equatable {
    var status: String
    var summary: AILibraryBuildSummary
    var library: AILibraryStatus
}

struct AILibraryBuildSummary: Codable, Equatable {
    var candidateCount: Int
    var rebuiltCount: Int
    var unchangedCount: Int
    var skippedCount: Int
    var errorCount: Int
    var missingCount: Int
    var chunkCount: Int
}

struct AILibrarySearchResult: Codable, Equatable {
    var query: String
    var retrievalMode: String
    var results: [AIRAGSource]
    var message: String?
}

struct AIDocumentSource: Codable, Equatable, Identifiable {
    var kind: String?
    var sourceId: String
    var fileId: String
    var projectId: String
    var groupId: String?
    var relativePath: String
    var absolutePath: String
    var fileName: String
    var `extension`: String
    var size: Int
    var sha256: String
    var mtimeNs: Int
    var mimeType: String
    var contentStatus: String
    var chunkCount: Int
    var lastError: String
    var indexedAt: Double
    var updatedAt: Double
    var realFileDeleted: Bool

    var id: String { sourceId }

    var statusLabel: String {
        switch contentStatus {
        case "indexed": return "已索引"
        case "pending": return "待构建"
        case "stale": return "需更新"
        case "missing": return "文件缺失"
        case "skipped": return "已跳过"
        case "error": return "错误"
        case "deleted_local": return "本机已删除"
        default: return contentStatus.isEmpty ? "-" : contentStatus
        }
    }
}

struct AILibrarySourceListResult: Codable, Equatable {
    var groupId: String
    var projectId: String
    var status: String
    var query: String
    var sources: [AIDocumentSource]
    var count: Int
    var realFilesDeleted: Bool
}

struct AILibrarySourceMutationResult: Codable, Equatable {
    var deleted: Bool?
    var restored: Bool?
    var source: AIDocumentSource
    var needsBuild: Bool?
    var realFileDeleted: Bool
    var projectIndexDeleted: Bool?
    var scope: String
}

struct AIRAGAnswerResult: Codable, Equatable {
    var answer: String
    var conversationId: String
    var userMessage: AIConversationMessage?
    var assistantMessage: AIConversationMessage?
    var sources: [AIRAGSource]
    var retrieval: AIRAGRetrieval
    var provider: AIRAGProvider
    var privacyPolicy: AIRAGPrivacyPolicy
}

struct AIRAGSource: Codable, Equatable, Identifiable {
    var kind: String?
    var sourceIndex: String?
    var fileId: String
    var sourceId: String
    var chunkId: String?
    var projectId: String?
    var groupId: String?
    var relativePath: String
    var absolutePath: String
    var fileName: String?
    var `extension`: String?
    var size: Int?
    var sha256: String
    var mtimeNs: Int
    var chunkIndex: Int?
    var lineStart: Int
    var lineEnd: Int
    var charStart: Int?
    var charEnd: Int?
    var tokenEstimate: Int?
    var snippet: String
    var score: Double

    var id: String { chunkId ?? sourceId }
    var displaySourceIndex: String { sourceIndex ?? "" }
}

struct AIRAGRetrieval: Codable, Equatable {
    var mode: String
    var query: String
    var candidateCount: Int
    var sourceCount: Int
}

struct AIRAGProvider: Codable, Equatable {
    var providerType: String
    var baseUrl: String
    var model: String
    var hasApiKey: Bool
}

struct AIRAGPrivacyPolicy: Codable, Equatable {
    var scope: String
    var uploadPolicy: String
    var noCommandExecution: Bool
    var noFileModification: Bool
    var embeddingEnabled: Bool
}

struct AIConversationSummary: Codable, Equatable, Identifiable {
    var conversationId: String
    var profile: String
    var groupId: String
    var projectId: String
    var providerType: String
    var model: String
    var title: String
    var createdAt: Double
    var updatedAt: Double
    var messageCount: Int

    var id: String { conversationId }
}

struct AIConversationMessage: Codable, Equatable, Identifiable {
    var messageId: String
    var conversationId: String
    var role: String
    var content: String
    var createdAt: Double

    var id: String { messageId }
}

struct GroupInfo: Codable, Equatable {
    var id: String
    var name: String
    var creatorId: String?
}

struct ProjectInfo: Codable, Equatable {
    var id: String
    var groupId: String
    var name: String
    var rootSharedFolderId: String
    var status: String
}

struct SharedFolderInfo: Codable, Equatable {
    var id: String
    var name: String
    var groupId: String
    var localPath: String
    var syncthingFolderId: String
    var status: String
    var projectId: String
    var lastStatus: String
    var lastCompletion: Double
    var lastError: String
}

struct SyncDeviceInfo: Codable, Equatable, Identifiable {
    var groupId: String
    var userId: String
    var syncthingDeviceId: String
    var displayName: String
    var status: String

    var id: String { "\(groupId)-\(userId)-\(syncthingDeviceId)" }
}

struct SyncOverview: Codable, Equatable, Identifiable {
    var groupId: String
    var group: GroupInfo?
    var project: ProjectInfo?
    var sharedFolder: SharedFolderInfo?
    var devices: [SyncDeviceInfo]
    var configured: Bool
    var status: String
    var completion: Double
    var error: String
    var localPathExists: Bool

    var id: String { groupId }
    var title: String { project?.name ?? group?.name ?? groupId }
}

struct SyncUnbindResult: Codable, Equatable {
    var groupId: String
    var projectId: String
    var localPath: String
    var localPathExists: Bool
    var previousSyncthingFolderId: String
    var syncthingFolderRemoved: Bool
    var localOnly: Bool
    var restartRequired: Bool
    var restartCheckError: String
    var projectIndex: ProjectIndexClearResult?
    var aiDocumentLibrary: AILibraryClearResult?
    var binding: SyncBindingDeleteResult?
    var realFilesDeleted: Bool
    var groupDeleted: Bool
    var messagesDeleted: Int
    var scope: String
}

struct ProjectIndexClearResult: Codable, Equatable {
    var groupId: String
    var projectId: String
    var deletedFiles: Int
    var deletedRuns: Int
    var realFilesDeleted: Bool
    var scope: String
}

struct AILibraryClearResult: Codable, Equatable {
    var groupId: String
    var projectId: String
    var deletedSources: Int
    var deletedChunks: Int
    var deletedFts: Int
    var deletedEmbeddings: Int
    var realFilesDeleted: Bool
    var scope: String
}

struct SyncBindingDeleteResult: Codable, Equatable {
    var groupId: String
    var projectIds: [String]
    var sharedFolderIds: [String]
    var deletedProjects: Int
    var deletedSharedFolders: Int
    var deletedSyncDevices: Int
    var groupMetadataUpdated: Bool
    var realFilesDeleted: Bool
    var messagesDeleted: Int
    var fileAttachmentsDeleted: Int
    var scope: String
}

struct EnvironmentCheckReport: Codable, Equatable {
    var status: String
    var projectRoot: String
    var profile: String
    var checkedAt: Double
    var checks: [EnvironmentCheckItem]

    var label: String {
        switch status {
        case "ok": return "就绪"
        case "warning": return "需注意"
        case "error": return "有错误"
        default: return status
        }
    }
}

struct EnvironmentCheckItem: Codable, Equatable, Identifiable {
    var key: String
    var title: String
    var status: String
    var message: String
    var repairHint: String

    var id: String { key }

    var systemImage: String {
        switch status {
        case "ok": return "checkmark.circle.fill"
        case "error": return "xmark.octagon.fill"
        default: return "exclamationmark.triangle.fill"
        }
    }
}

struct EnvironmentBootstrapResult: Codable, Equatable {
    var status: String
    var steps: [BootstrapStep]
    var logs: [BootstrapLogEntry]
    var nextActions: [String]
    var copyableCommands: [CopyableCommand]
    var venvPath: String?
    var pythonExecutable: String?
    var plannedCommands: [String]?
    var report: EnvironmentCheckReport?

    static let empty = EnvironmentBootstrapResult(
        status: "needs_action",
        steps: [],
        logs: [],
        nextActions: [],
        copyableCommands: [],
        venvPath: nil,
        pythonExecutable: nil,
        plannedCommands: nil,
        report: nil
    )

    var label: String {
        switch status {
        case "done": return "已完成"
        case "failed": return "失败"
        case "skippable": return "可稍后配置"
        default: return "需要操作"
        }
    }
}

struct BootstrapStep: Codable, Equatable, Identifiable {
    var key: String
    var title: String
    var status: String
    var message: String
    var repairHint: String

    var id: String { key }

    var label: String {
        switch status {
        case "done": return "已完成"
        case "failed": return "失败"
        case "skippable": return "可跳过"
        default: return "需要操作"
        }
    }

    var systemImage: String {
        switch status {
        case "done": return "checkmark.circle.fill"
        case "failed": return "xmark.octagon.fill"
        case "skippable": return "forward.circle.fill"
        default: return "exclamationmark.triangle.fill"
        }
    }
}

struct BootstrapLogEntry: Codable, Equatable, Identifiable {
    var level: String
    var message: String
    var command: String
    var detail: String
    var exitCode: Int?

    var id: String {
        "\(level)-\(message)-\(command)-\(detail)-\(exitCode ?? -999)"
    }
}

struct CopyableCommand: Codable, Equatable, Identifiable {
    var title: String
    var command: String

    var id: String { "\(title)-\(command)" }
}

struct ProjectIndexStatus: Codable, Equatable {
    var totalCount: Int
    var existingCount: Int
    var missingCount: Int
    var lastUpdatedAt: Double
    var lastRun: ProjectIndexRun?
    var projects: [ProjectIndexProjectCount]
    var tablesReady: Bool

    static let empty = ProjectIndexStatus(
        totalCount: 0,
        existingCount: 0,
        missingCount: 0,
        lastUpdatedAt: 0,
        lastRun: nil,
        projects: [],
        tablesReady: false
    )
}

struct ProjectIndexRun: Codable, Equatable, Identifiable {
    var id: String
    var projectId: String
    var groupId: String
    var sharedFolderId: String
    var rootPath: String
    var startedAt: Double
    var completedAt: Double
    var status: String
    var scannedCount: Int
    var updatedCount: Int
    var deletedCount: Int
    var skippedCount: Int
    var errorSummary: String
}

struct ProjectIndexProjectCount: Codable, Equatable {
    var projectId: String
    var groupId: String
    var fileCount: Int
}

struct ProjectFileSearchResult: Codable, Equatable, Identifiable {
    var id: String
    var projectId: String
    var groupId: String
    var sharedFolderId: String
    var projectName: String
    var groupName: String
    var rootPath: String
    var relativePath: String
    var absolutePath: String
    var fileName: String
    var `extension`: String
    var size: Int
    var mtime: Double
    var mtimeNs: Int
    var sha256: String
    var mimeType: String
    var fileKind: String
    var exists: Bool
    var hashStatus: String
    var indexedAt: Double
    var updatedAt: Double

    var displayProject: String {
        projectName.isEmpty ? groupName : projectName
    }
}

struct LauncherEvent: Codable, Equatable {
    var type: String
    var timestamp: Double
    var state: String?
    var userId: String?
    var displayName: String?
    var profile: String?
    var stage: String?
    var error: String?
    var exitCode: Int?
}

struct LogEntry: Identifiable, Equatable {
    let id = UUID()
    let date: Date
    let stream: String
    let text: String
    let event: LauncherEvent?
}
