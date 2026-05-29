import SwiftUI

struct SecurityStatusView: View {
    @EnvironmentObject private var viewModel: LauncherViewModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                header
                encryptionSection
                hubSection
                tempFileSection
                syncthingSection
                aiSection
            }
            .padding()
        }
        .task {
            await viewModel.refreshSecurityStatus()
        }
    }

    private var header: some View {
        HStack {
            Label("安全状态 / 隐私状态", systemImage: "lock.shield")
                .font(.title2.weight(.semibold))
            Spacer()
            Button {
                Task { await viewModel.refreshSecurityStatus() }
            } label: {
                Label("刷新", systemImage: "arrow.clockwise")
            }
        }
    }

    private var encryptionSection: some View {
        GroupBox("聊天加密") {
            VStack(alignment: .leading, spacing: 10) {
                SecurityStatusRow(title: "当前模式", value: viewModel.securityStatus.encryption.currentMode)
                SecurityStatusRow(title: "direct encrypted v2", value: viewModel.securityStatus.encryption.directEncryptedV2 ? "可用" : "不可用")
                SecurityStatusRow(title: "group encrypted v1", value: viewModel.securityStatus.encryption.groupEncryptedV1 ? "可用" : "不可用")
                SecurityStatusRow(title: "Replay protection", value: viewModel.securityStatus.encryption.replayProtection ? "启用" : "未启用")
            }
            .padding(.top, 4)
        }
    }

    private var hubSection: some View {
        GroupBox("Hub 本地化") {
            VStack(alignment: .leading, spacing: 10) {
                SecurityStatusRow(title: "Hub 目录", value: viewModel.hubAdminStatus.status.hubDir.isEmpty ? "-" : viewModel.hubAdminStatus.status.hubDir)
                SecurityStatusRow(title: "当前 Hub 主机", value: viewModel.hubAdminStatus.localHubRunning ? "是" : "否")
                SecurityStatusRow(title: "Admin 初始化", value: viewModel.hubAdminStatus.status.adminInitialized ? "是" : "否")
                SecurityStatusRow(title: "设备注册", value: "\(viewModel.hubAdminStatus.status.deviceCount)")
                SecurityStatusRow(title: "离线密文队列", value: "\(viewModel.hubAdminStatus.status.offlineQueueCount)")
                SecurityStatusRow(title: "本机设备", value: viewModel.deviceSummary.deviceFingerprint.isEmpty ? "-" : viewModel.deviceSummary.deviceFingerprint)
            }
            .padding(.top, 4)
        }
    }

    private var tempFileSection: some View {
        GroupBox("临时文件服务") {
            VStack(alignment: .leading, spacing: 10) {
                SecurityStatusRow(title: "状态", value: viewModel.securityStatus.tempFiles.label)
                SecurityStatusRow(title: "URL", value: viewModel.securityStatus.tempFiles.url.isEmpty ? "-" : viewModel.securityStatus.tempFiles.url)
                SecurityStatusRow(title: "默认过期", value: ttlLabel(viewModel.securityStatus.tempFiles.ttlSeconds))
                SecurityStatusRow(title: "Hub 密文数量", value: "\(viewModel.securityStatus.tempFiles.fileCount)")
                if !viewModel.securityStatus.tempFiles.message.isEmpty {
                    Text(viewModel.securityStatus.tempFiles.message)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                }
            }
            .padding(.top, 4)
        }
    }

    private var syncthingSection: some View {
        GroupBox("Syncthing API") {
            VStack(alignment: .leading, spacing: 10) {
                SecurityStatusRow(title: "状态", value: viewModel.securityStatus.syncthing?.label ?? "未检测")
                SecurityStatusRow(title: "API Key", value: viewModel.syncthingSettings.apiKey.isEmpty ? "未配置" : "已配置")
                SecurityStatusRow(title: "Device ID", value: viewModel.securityStatus.syncthing?.deviceId?.isEmpty == false ? viewModel.securityStatus.syncthing?.deviceId ?? "-" : "-")
                if let error = viewModel.securityStatus.syncthing?.error, !error.isEmpty {
                    Text(error)
                        .font(.caption)
                        .foregroundStyle(.red)
                        .textSelection(.enabled)
                }
            }
            .padding(.top, 4)
        }
    }

    private var aiSection: some View {
        GroupBox("AI Provider") {
            VStack(alignment: .leading, spacing: 10) {
                SecurityStatusRow(title: "状态", value: viewModel.securityStatus.ai.configured ? "已配置" : "未配置")
                SecurityStatusRow(title: "Provider", value: viewModel.securityStatus.ai.providerLabel)
                SecurityStatusRow(title: "类型", value: locationLabel(viewModel.securityStatus.ai.providerLocation))
                SecurityStatusRow(title: "API Key", value: viewModel.securityStatus.ai.hasApiKey ? "已配置" : "未配置")
                SecurityStatusRow(title: "模型", value: viewModel.securityStatus.ai.model.isEmpty ? "-" : viewModel.securityStatus.ai.model)
                if viewModel.securityStatus.ai.providerType == "lm_studio" {
                    SecurityStatusRow(title: "自动加载", value: (viewModel.securityStatus.ai.autoLoadLocalModel ?? true) ? "开启" : "关闭")
                    SecurityStatusRow(title: "模型 Key", value: viewModel.securityStatus.ai.lmstudioModelKey?.isEmpty == false ? viewModel.securityStatus.ai.lmstudioModelKey ?? "-" : "使用 Model 字段")
                }
                SecurityStatusRow(title: "最近文件摘要", value: lastSummaryLabel)
            }
            .padding(.top, 4)
        }
    }

    private var lastSummaryLabel: String {
        guard let last = viewModel.lastAIFileSummary else {
            return "暂无"
        }
        return "\(last.fileName) -> \(last.providerLabel) / \(last.providerLocation)"
    }

    private func ttlLabel(_ seconds: Int) -> String {
        guard seconds > 0 else { return "-" }
        return "\(max(1, seconds / 60)) 分钟"
    }

    private func locationLabel(_ value: String) -> String {
        switch value {
        case "local": return "本地"
        case "remote": return "远程"
        default: return "未知"
        }
    }
}

private struct SecurityStatusRow: View {
    let title: String
    let value: String

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(title)
                .foregroundStyle(.secondary)
                .frame(width: 150, alignment: .leading)
            Text(value)
                .textSelection(.enabled)
            Spacer(minLength: 0)
        }
    }
}
