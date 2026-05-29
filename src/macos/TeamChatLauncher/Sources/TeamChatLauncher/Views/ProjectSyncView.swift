import SwiftUI

struct ProjectSyncView: View {
    @EnvironmentObject private var viewModel: LauncherViewModel
    @State private var itemPendingUnbind: SyncOverview?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Button {
                    Task { await viewModel.refreshSync() }
                } label: {
                    Label("刷新", systemImage: "arrow.clockwise")
                }
                Button {
                    Task { await viewModel.scanProjectIndex() }
                } label: {
                    Label("建立索引", systemImage: "doc.text.magnifyingglass")
                }
                .disabled(viewModel.selectedProfile == nil || viewModel.isIndexing)
                Button {
                    viewModel.openSyncthingWebUI()
                } label: {
                    Label("Syncthing Web UI", systemImage: "safari")
                }
                Spacer()
                Text("\(viewModel.syncItems.count) 个项目")
                    .foregroundStyle(.secondary)
            }

            searchPanel

            if viewModel.syncItems.isEmpty {
                EmptyStateView(title: "暂无项目同步绑定", systemImage: "folder.badge.questionmark")
            } else {
                List(viewModel.syncItems) { item in
                    SyncOverviewRow(item: item) { selected in
                        itemPendingUnbind = selected
                    }
                        .contextMenu {
                            if let path = item.sharedFolder?.localPath, !path.isEmpty {
                                Button("打开项目文件夹") {
                                    viewModel.openProjectFolder(path)
                                }
                            }
                            Button("刷新此项目") {
                                Task { await viewModel.refreshSync(groupID: item.groupId) }
                            }
                            Button("索引此项目") {
                                Task { await viewModel.scanProjectIndex(groupID: item.groupId) }
                            }
                            Button("搜索此项目") {
                                Task { await viewModel.searchProjectFiles(groupID: item.groupId) }
                            }
                            Button("删除绑定", role: .destructive) {
                                itemPendingUnbind = item
                            }
                        }
                }
                .listStyle(.inset)
            }
        }
        .padding()
        .alert("删除项目绑定？", isPresented: Binding(
            get: { itemPendingUnbind != nil },
            set: { if !$0 { itemPendingUnbind = nil } }
        ), presenting: itemPendingUnbind) { item in
            Button("取消", role: .cancel) {
                itemPendingUnbind = nil
            }
            Button("删除绑定", role: .destructive) {
                Task {
                    await viewModel.unbindSyncProject(item)
                    itemPendingUnbind = nil
                }
            }
        } message: { item in
            Text("只移除当前 profile 的项目绑定、索引和 AI 文档库记录，不删除本地文件夹，不影响群成员。若已配置 Syncthing Folder，仅从本机 Syncthing 配置移除：\(item.title)")
        }
    }

    private var searchPanel: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                TextField("搜索文件名或相对路径", text: $viewModel.fileSearchQuery)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit {
                        Task { await viewModel.searchProjectFiles() }
                    }
                TextField("扩展名", text: $viewModel.fileSearchExtension)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 90)
                Button {
                    Task { await viewModel.searchProjectFiles() }
                } label: {
                    Label("搜索", systemImage: "magnifyingglass")
                }
                .disabled(viewModel.selectedProfile == nil || viewModel.isSearchingFiles)
            }

            HStack {
                Text("已索引 \(viewModel.projectIndexStatus.existingCount) 个文件")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                if viewModel.projectIndexStatus.missingCount > 0 {
                    Text("缺失 \(viewModel.projectIndexStatus.missingCount)")
                        .font(.caption)
                        .foregroundStyle(.orange)
                }
                if viewModel.isIndexing || viewModel.isSearchingFiles {
                    ProgressView()
                        .controlSize(.small)
                }
            }

            if !viewModel.fileSearchResults.isEmpty {
                List(viewModel.fileSearchResults) { result in
                    ProjectFileResultRow(result: result)
                }
                .frame(minHeight: 140, maxHeight: 240)
                .listStyle(.inset)
            }
        }
    }
}

private struct SyncOverviewRow: View {
    @EnvironmentObject private var viewModel: LauncherViewModel
    let item: SyncOverview
    let onRequestUnbind: (SyncOverview) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Image(systemName: item.localPathExists ? "folder.fill" : "folder.badge.questionmark")
                    .foregroundStyle(item.localPathExists ? Color.secondary : Color.red)
                    .frame(width: 20)
                VStack(alignment: .leading, spacing: 2) {
                    Text(item.title)
                        .font(.headline)
                    Text(item.group?.name ?? item.groupId)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Text(displayStatus(item.status))
                    .foregroundStyle(item.status == "error" ? .red : .secondary)
                Text("\(item.completion, specifier: "%.0f")%")
                    .monospacedDigit()
            }

            if let folder = item.sharedFolder {
                Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 4) {
                    GridRow {
                        Text("本地路径").foregroundStyle(.secondary)
                        Text(folder.localPath.isEmpty ? "-" : folder.localPath)
                            .lineLimit(1)
                            .textSelection(.enabled)
                    }
                    GridRow {
                        Text("Folder ID").foregroundStyle(.secondary)
                        Text(folder.syncthingFolderId.isEmpty ? "未配置" : folder.syncthingFolderId)
                            .textSelection(.enabled)
                    }
                    GridRow {
                        Text("设备").foregroundStyle(.secondary)
                        Text(item.devices.isEmpty ? "未添加" : item.devices.map(\.displayNameOrId).joined(separator: ", "))
                    }
                }
                HStack {
                    Button {
                        viewModel.openProjectFolder(folder.localPath)
                    } label: {
                        Label("打开文件夹", systemImage: "folder")
                    }
                    .disabled(folder.localPath.isEmpty || !item.localPathExists)

                    Button {
                        Task { await viewModel.refreshSync(groupID: item.groupId) }
                    } label: {
                        Label("刷新状态", systemImage: "arrow.clockwise")
                    }

                    Button {
                        Task { await viewModel.scanProjectIndex(groupID: item.groupId) }
                    } label: {
                        Label("索引", systemImage: "doc.text.magnifyingglass")
                    }
                    .disabled(viewModel.isIndexing)

                    Button(role: .destructive) {
                        onRequestUnbind(item)
                    } label: {
                        Label("删除绑定", systemImage: "trash")
                    }
                    .disabled(viewModel.isUnbindingSync)
                }
            }

            if !item.error.isEmpty {
                Text(item.error)
                    .font(.caption)
                    .foregroundStyle(.red)
                    .textSelection(.enabled)
            }
        }
        .padding(.vertical, 8)
    }

    private func displayStatus(_ value: String) -> String {
        switch value {
        case "configured": return "已配置"
        case "local_bound": return "已绑定"
        case "syncing": return "同步中"
        case "synced": return "已同步"
        case "stopped": return "已停止"
        case "error": return "错误"
        case "unconfigured": return "未配置"
        default: return value.isEmpty ? "-" : value
        }
    }
}

private struct ProjectFileResultRow: View {
    @EnvironmentObject private var viewModel: LauncherViewModel
    let result: ProjectFileSearchResult

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: result.exists ? "doc.text" : "doc.badge.clock")
                .foregroundStyle(result.exists ? Color.secondary : Color.orange)
                .frame(width: 20)
            VStack(alignment: .leading, spacing: 2) {
                Text(result.fileName)
                    .font(.headline)
                Text(result.relativePath)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .textSelection(.enabled)
                Text("\(result.displayProject)  \(formatBytes(result.size))  \(formatDate(result.mtime))")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Button {
                viewModel.openIndexedFile(result)
            } label: {
                Label("打开", systemImage: "arrow.up.right.square")
            }
            .disabled(!result.exists)
            Button {
                viewModel.revealIndexedFile(result)
            } label: {
                Label("Finder", systemImage: "folder")
            }
            .disabled(!result.exists)
        }
        .padding(.vertical, 4)
    }

    private func formatBytes(_ value: Int) -> String {
        ByteCountFormatter.string(fromByteCount: Int64(value), countStyle: .file)
    }

    private func formatDate(_ timestamp: Double) -> String {
        guard timestamp > 0 else { return "-" }
        let formatter = DateFormatter()
        formatter.dateStyle = .short
        formatter.timeStyle = .short
        return formatter.string(from: Date(timeIntervalSince1970: timestamp))
    }
}

private extension SyncDeviceInfo {
    var displayNameOrId: String {
        displayName.isEmpty ? syncthingDeviceId : displayName
    }
}
