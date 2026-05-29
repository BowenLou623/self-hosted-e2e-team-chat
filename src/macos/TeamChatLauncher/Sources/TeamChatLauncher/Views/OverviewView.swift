import AppKit
import SwiftUI

struct OverviewView: View {
    @ObservedObject var processController: ChatProcessController

    var body: some View {
        OverviewContent(processController: processController)
    }
}

private struct OverviewContent: View {
    @EnvironmentObject private var viewModel: LauncherViewModel
    @ObservedObject var processController: ChatProcessController

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                header
                projectRootNotice
                statusGrid
                recentProjects
                actions
                if !viewModel.statusMessage.isEmpty {
                    Text(viewModel.statusMessage)
                        .foregroundStyle(.secondary)
                }
            }
            .padding()
        }
    }

    private var header: some View {
        LauncherCard(title: "工作台", systemImage: "macwindow.and.cursorarrow") {
            HStack(alignment: .center, spacing: 18) {
                VStack(alignment: .leading, spacing: 6) {
                    Text(viewModel.selectedProfile?.displayTitle ?? "未选择 profile")
                        .font(.system(size: 30, weight: .bold))
                    Text("本地优先的安装、配置与启动入口")
                        .foregroundStyle(.secondary)
                    Text(viewModel.pythonStatus.summary)
                        .font(.caption)
                        .foregroundStyle(viewModel.pythonStatus.canLaunchClient ? Color.secondary : Color.orange)
                }
                Spacer()
                StatusBadge(label: processController.state.label, status: processController.isRunning ? "running" : "skippable")
            }
        }
    }

    @ViewBuilder
    private var projectRootNotice: some View {
        if !ProjectRootResolver.isValidProjectRoot(viewModel.config.projectRoot) {
            HStack(spacing: 12) {
                Label("需要选择 Python 项目根目录", systemImage: "folder.badge.questionmark")
                    .foregroundStyle(.orange)
                Button {
                    chooseProjectRoot()
                } label: {
                    Label("选择目录", systemImage: "folder")
                }
            }
        }
    }

    private var statusGrid: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 240), spacing: 14)], spacing: 14) {
            LauncherCard(title: "Profile", systemImage: "person.crop.circle") {
                Text(viewModel.selectedProfile?.profile ?? "未选择")
                    .font(.title3.weight(.semibold))
                Text(viewModel.selectedProfile?.userId.isEmpty == false ? viewModel.selectedProfile?.userId ?? "" : "尚未初始化 identity")
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            LauncherCard(title: "Hub", systemImage: "network") {
                Text(viewModel.launcherSettings.hubAddress)
                    .font(.title3.weight(.semibold))
                    .textSelection(.enabled)
                Text(viewModel.hubProbeMessage.isEmpty ? "启动前会自动发现局域网 Hub" : viewModel.hubProbeMessage)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
            LauncherCard(title: "Syncthing", systemImage: "arrow.triangle.2.circlepath") {
                StatusBadge(label: viewModel.syncthingStatus?.label ?? "未检测", status: viewModel.syncthingStatus?.state == "connected" ? "connected" : "needs_action")
                Text(viewModel.syncthingSettings.baseUrl.isEmpty ? SyncthingSettings.fallback.baseUrl : viewModel.syncthingSettings.baseUrl)
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
            }
            LauncherCard(title: "AI Provider", systemImage: "sparkles") {
                Text(viewModel.aiSettings.providerLabel)
                    .font(.title3.weight(.semibold))
                Text(viewModel.aiSettings.providerLocation == "远程" ? "远程 provider 会接收用户确认的上下文" : "本地优先")
                    .foregroundStyle(.secondary)
            }
            LauncherCard(title: "Python 环境", systemImage: "chevron.left.forwardslash.chevron.right") {
                StatusBadge(label: viewModel.pythonStatus.canLaunchClient ? "可启动" : "需修复", status: viewModel.pythonStatus.canLaunchClient ? "done" : "needs_action")
                Text(viewModel.pythonStatus.version.isEmpty ? viewModel.config.pythonExecutable : "Python \(viewModel.pythonStatus.version)")
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
                    .textSelection(.enabled)
            }
            LauncherCard(title: "项目索引", systemImage: "doc.text.magnifyingglass") {
                Text("\(viewModel.projectIndexStatus.existingCount)")
                    .font(.title2.weight(.bold))
                Text("已索引文件")
                    .foregroundStyle(.secondary)
            }
        }
    }

    private var recentProjects: some View {
        LauncherCard(title: "最近项目", systemImage: "folder.badge.gearshape") {
            if viewModel.syncItems.isEmpty {
                Label("暂无项目同步绑定", systemImage: "tray")
                    .foregroundStyle(.secondary)
            } else {
                VStack(spacing: 10) {
                    ForEach(Array(viewModel.syncItems.prefix(3))) { item in
                        HStack {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(item.title)
                                    .font(.headline)
                                Text(item.sharedFolder?.localPath ?? "未绑定本机路径")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(1)
                            }
                            Spacer()
                            StatusBadge(label: item.status.isEmpty ? "未检测" : item.status, status: item.configured ? "done" : "needs_action")
                        }
                    }
                }
            }
        }
    }

    private var actions: some View {
        LauncherCard(title: "快速操作", systemImage: "bolt.fill") {
            HStack {
                Button {
                    viewModel.startClient()
                } label: {
                    Label(viewModel.isResolvingHubForLaunch ? "正在连接 Hub" : "启动聊天客户端", systemImage: "play.fill")
                }
                .buttonStyle(.borderedProminent)
                .disabled(processController.isRunning || viewModel.isResolvingHubForLaunch || !viewModel.pythonStatus.canLaunchClient)

                Button {
                    viewModel.stopClient()
                } label: {
                    Label("停止客户端", systemImage: "stop.fill")
                }
                .disabled(!processController.isRunning)

                Button {
                    Task {
                        await viewModel.detectSyncthing()
                        await viewModel.refreshSync()
                        await viewModel.refreshProjectIndexStatus()
                    }
                } label: {
                    Label("刷新同步状态", systemImage: "arrow.clockwise")
                }
            }
        }
    }

    private func chooseProjectRoot() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        if panel.runModal() == .OK, let url = panel.url {
            if ProjectRootResolver.isValidProjectRoot(url.path) {
                viewModel.config.projectRoot = url.path
                viewModel.config.venvPath = url.appendingPathComponent(".venv").path
                viewModel.saveGlobalConfig()
                Task { await viewModel.refreshProfiles() }
            } else {
                viewModel.statusMessage = "请选择包含 src/app/main.py 的项目根目录"
            }
        }
    }
}

struct StatusMetric: View {
    let title: String
    let value: String
    let systemImage: String

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: systemImage)
                .foregroundStyle(.secondary)
                .frame(width: 18)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Text(value)
                    .lineLimit(1)
            }
            .frame(minWidth: 180, alignment: .leading)
        }
    }
}
