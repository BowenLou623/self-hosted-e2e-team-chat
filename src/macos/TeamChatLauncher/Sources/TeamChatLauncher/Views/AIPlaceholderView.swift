import SwiftUI

struct AIPlaceholderView: View {
    @EnvironmentObject private var viewModel: LauncherViewModel
    @State private var sourcePendingDelete: AIDocumentSource?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                header
                providerSection
                Divider()
                projectSection
                librarySection
                Divider()
                ragSection
                Divider()
                fileSection
                contextPreviewSection
                resultSection
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding()
        .onAppear {
            if viewModel.aiSelectedGroupID.isEmpty, let first = viewModel.syncItems.first {
                viewModel.aiSelectedGroupID = first.groupId
            }
            Task {
                await viewModel.refreshAILibraryStatus()
                await viewModel.refreshAILibrarySources()
                await viewModel.refreshAIConversations()
            }
        }
        .alert("删除本机 AI 索引？", isPresented: Binding(
            get: { sourcePendingDelete != nil },
            set: { if !$0 { sourcePendingDelete = nil } }
        ), presenting: sourcePendingDelete) { source in
            Button("取消", role: .cancel) {
                sourcePendingDelete = nil
            }
            Button("删除索引", role: .destructive) {
                Task {
                    await viewModel.deleteAIDocumentSource(source)
                    sourcePendingDelete = nil
                }
            }
        } message: { source in
            Text("只删除当前 profile 的本机 AI 文档库记录、chunks 和 FTS，不删除真实文件：\(source.relativePath)")
        }
    }

    private var header: some View {
        HStack {
            Label("AI 项目助手", systemImage: "sparkles")
                .font(.title2.weight(.semibold))
            Spacer()
            if viewModel.isAIWorking {
                ProgressView()
                    .controlSize(.small)
            }
        }
    }

    private var providerSection: some View {
        Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 8) {
            GridRow {
                Text("Provider").foregroundStyle(.secondary)
                Picker("Provider", selection: $viewModel.aiSettings.providerType) {
                    Text("未选择").tag("")
                    Text("Ollama").tag("ollama")
                    Text("LM Studio").tag("lm_studio")
                    Text("OpenAI-compatible").tag("openai_compatible")
                }
                .labelsHidden()
                .frame(maxWidth: 260)
                .onChange(of: viewModel.aiSettings.providerType) { _ in
                    viewModel.applyAIProviderDefaultsIfNeeded()
                }
            }
            GridRow {
                Text("Base URL").foregroundStyle(.secondary)
                TextField("http://127.0.0.1:11434", text: $viewModel.aiSettings.baseUrl)
                    .textFieldStyle(.roundedBorder)
                    .onChange(of: viewModel.aiSettings.baseUrl) { _ in
                        viewModel.markAISettingsDirty()
                    }
            }
            GridRow {
                Text("Model").foregroundStyle(.secondary)
                TextField("model name", text: $viewModel.aiSettings.model)
                    .textFieldStyle(.roundedBorder)
                    .onChange(of: viewModel.aiSettings.model) { _ in
                        viewModel.markAISettingsDirty()
                    }
            }
            if viewModel.aiSettings.providerType == "lm_studio" {
                GridRow {
                    Text("LM Studio").foregroundStyle(.secondary)
                    Toggle("自动启动并加载本机模型", isOn: $viewModel.aiSettings.autoLoadLocalModel)
                        .toggleStyle(.checkbox)
                        .onChange(of: viewModel.aiSettings.autoLoadLocalModel) { _ in
                            viewModel.markAISettingsDirty()
                        }
                }
                GridRow {
                    Text("模型 Key").foregroundStyle(.secondary)
                    TextField("留空则使用 Model 字段", text: $viewModel.aiSettings.lmstudioModelKey)
                        .textFieldStyle(.roundedBorder)
                        .onChange(of: viewModel.aiSettings.lmstudioModelKey) { _ in
                            viewModel.markAISettingsDirty()
                        }
                }
                GridRow {
                    Text("lms 路径").foregroundStyle(.secondary)
                    TextField("自动查找 lms", text: $viewModel.aiSettings.lmsPath)
                        .textFieldStyle(.roundedBorder)
                        .onChange(of: viewModel.aiSettings.lmsPath) { _ in
                            viewModel.markAISettingsDirty()
                        }
                }
            }
            GridRow {
                Text("API Key").foregroundStyle(.secondary)
                SecureField("可选，留空则不修改", text: $viewModel.aiAPIKeyInput)
                    .textFieldStyle(.roundedBorder)
            }
            GridRow {
                Text("限制").foregroundStyle(.secondary)
                HStack {
                    Stepper("超时 \(viewModel.aiSettings.timeoutSeconds, specifier: "%.0f") 秒", value: $viewModel.aiSettings.timeoutSeconds, in: 2...120, step: 1)
                        .onChange(of: viewModel.aiSettings.timeoutSeconds) { _ in
                            viewModel.markAISettingsDirty()
                        }
                    Stepper("文件 \(viewModel.aiSettings.maxFileBytes / 1024) KB", value: $viewModel.aiSettings.maxFileBytes, in: 1024...(1024 * 1024), step: 16 * 1024)
                        .onChange(of: viewModel.aiSettings.maxFileBytes) { _ in
                            viewModel.markAISettingsDirty()
                        }
                    Stepper("文档库 \(viewModel.aiSettings.maxDocumentBytes / 1024) KB", value: $viewModel.aiSettings.maxDocumentBytes, in: 1024...(2 * 1024 * 1024), step: 64 * 1024)
                        .onChange(of: viewModel.aiSettings.maxDocumentBytes) { _ in
                            viewModel.markAISettingsDirty()
                        }
                }
            }
            GridRow {
                Text("RAG").foregroundStyle(.secondary)
                HStack {
                    Stepper("上下文 \(viewModel.aiSettings.ragMaxContextChars)", value: $viewModel.aiSettings.ragMaxContextChars, in: 2000...40000, step: 1000)
                        .onChange(of: viewModel.aiSettings.ragMaxContextChars) { _ in
                            viewModel.markAISettingsDirty()
                        }
                    Stepper("Chunk \(viewModel.aiSettings.ragMaxChunks)", value: $viewModel.aiSettings.ragMaxChunks, in: 1...20, step: 1)
                        .onChange(of: viewModel.aiSettings.ragMaxChunks) { _ in
                            viewModel.markAISettingsDirty()
                        }
                    Stepper("轮次 \(viewModel.aiSettings.conversationRecentTurns)", value: $viewModel.aiSettings.conversationRecentTurns, in: 1...20, step: 1)
                        .onChange(of: viewModel.aiSettings.conversationRecentTurns) { _ in
                            viewModel.markAISettingsDirty()
                        }
                    Text("Embedding: 预留 / 未启用")
                        .foregroundStyle(.secondary)
                }
            }
            GridRow {
                Text("")
                HStack {
                    Button {
                        Task { await viewModel.saveAISettings() }
                    } label: {
                        Label("保存配置", systemImage: "square.and.arrow.down")
                    }
                    Button {
                        Task { await viewModel.testAIConnection() }
                    } label: {
                        Label("测试连接", systemImage: "network")
                    }
                    .disabled(viewModel.isAIWorking)
                    Text(viewModel.aiSettingsDirty ? "未保存的配置会在调用前自动保存" : "配置已同步")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    if !viewModel.aiTestResult.isEmpty {
                        Text(viewModel.aiTestResult)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                    }
                }
            }
            if viewModel.aiSettings.providerLocation == "远程" {
                GridRow {
                    Text("")
                    Label("远程 provider 只会收到检索命中的片段和最近对话。", systemImage: "exclamationmark.triangle")
                        .foregroundStyle(.orange)
                }
            }
        }
    }

    private var projectSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Picker("项目", selection: $viewModel.aiSelectedGroupID) {
                    Text("请选择项目").tag("")
                    ForEach(viewModel.syncItems) { item in
                        Text(item.title).tag(item.groupId)
                    }
                }
                .frame(maxWidth: 360)
                .onChange(of: viewModel.aiSelectedGroupID) { _ in
                    viewModel.startNewAIConversation()
                    Task {
                        await viewModel.refreshAILibraryStatus()
                        await viewModel.refreshAILibrarySources()
                        await viewModel.refreshAIConversations()
                    }
                }
                Button {
                    Task { await viewModel.summarizeAIProject() }
                } label: {
                    Label("生成项目说明", systemImage: "text.badge.star")
                }
                .disabled(viewModel.aiSelectedGroupID.isEmpty || viewModel.isAIWorking)
                Toggle("包含部分文件内容", isOn: $viewModel.aiIncludeFileSnippets)
                    .toggleStyle(.checkbox)
                Spacer()
                Text("\(viewModel.projectIndexStatus.existingCount) 个已索引文件")
                    .foregroundStyle(.secondary)
            }
        }
    }

    private var librarySection: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Label("本地文档库", systemImage: "books.vertical")
                    .font(.subheadline.weight(.semibold))
                Spacer()
                Text("\(viewModel.aiLibraryStatus.indexedSourceCount)/\(viewModel.aiLibraryStatus.candidateCount) 文件  \(viewModel.aiLibraryStatus.chunkCount) chunks")
                    .foregroundStyle(.secondary)
                if (viewModel.aiLibraryStatus.deletedLocalCount ?? 0) > 0 {
                    Text("本机删除 \(viewModel.aiLibraryStatus.deletedLocalCount ?? 0)")
                        .foregroundStyle(.secondary)
                }
                if viewModel.aiLibraryStatus.pendingCount > 0 {
                    Text("待处理 \(viewModel.aiLibraryStatus.pendingCount)")
                        .foregroundStyle(.orange)
                }
                if viewModel.aiLibraryStatus.staleCount > 0 {
                    Text("需更新 \(viewModel.aiLibraryStatus.staleCount)")
                        .foregroundStyle(.orange)
                }
                Button {
                    Task {
                        await viewModel.refreshAILibraryStatus()
                        await viewModel.refreshAILibrarySources()
                    }
                } label: {
                    Label("刷新", systemImage: "arrow.clockwise")
                }
                Button {
                    Task { await viewModel.buildAILibrary() }
                } label: {
                    Label("构建文档库", systemImage: "square.stack.3d.down.right")
                }
                .disabled(viewModel.aiSelectedGroupID.isEmpty || viewModel.isAIWorking)
            }

            HStack {
                TextField("全文搜索文档库", text: $viewModel.aiLibrarySearchQuery)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit {
                        Task { await viewModel.searchAILibrary() }
                    }
                Button {
                    Task { await viewModel.searchAILibrary() }
                } label: {
                    Label("检索", systemImage: "magnifyingglass")
                }
                .disabled(viewModel.isAIWorking)
            }

            HStack {
                Label("文档记录", systemImage: "doc.text.magnifyingglass")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                Text("删除只影响本机 AI 索引，不删除真实文件")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer()
                Button {
                    Task { await viewModel.refreshAILibrarySources() }
                } label: {
                    Label("刷新记录", systemImage: "arrow.clockwise")
                }
                .disabled(viewModel.aiSelectedGroupID.isEmpty || viewModel.isAIWorking)
            }

            if !viewModel.aiDocumentSources.isEmpty {
                List(viewModel.aiDocumentSources) { source in
                    AIDocumentSourceRow(source: source) { selected in
                        sourcePendingDelete = selected
                    }
                }
                .frame(minHeight: 140, maxHeight: 260)
                .listStyle(.inset)
            } else {
                Text(viewModel.aiSelectedGroupID.isEmpty ? "请选择项目后查看本机文档库记录" : "暂无文档记录")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            if !viewModel.aiLibrarySearchResults.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(viewModel.aiLibrarySearchResults.prefix(6)) { source in
                        AISourceRow(source: source)
                    }
                }
            }
        }
    }

    private var ragSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Label("RAG 问答", systemImage: "bubble.left.and.text.bubble.right")
                    .font(.subheadline.weight(.semibold))
                Picker("对话", selection: Binding(
                    get: { viewModel.aiSelectedConversationID },
                    set: { newValue in
                        if newValue.isEmpty {
                            viewModel.startNewAIConversation()
                        } else {
                            Task { await viewModel.loadAIConversation(newValue) }
                        }
                    }
                )) {
                    Text("新对话").tag("")
                    ForEach(viewModel.aiConversations) { conversation in
                        Text(conversation.title.isEmpty ? conversation.conversationId : conversation.title)
                            .tag(conversation.conversationId)
                    }
                }
                .frame(maxWidth: 300)
                Button {
                    viewModel.startNewAIConversation()
                } label: {
                    Label("新建", systemImage: "plus")
                }
                Button {
                    Task { await viewModel.clearAIConversation() }
                } label: {
                    Label("清空", systemImage: "clear")
                }
                .disabled(viewModel.aiSelectedConversationID.isEmpty)
                Button {
                    Task { await viewModel.deleteAIConversation() }
                } label: {
                    Label("删除", systemImage: "trash")
                }
                .disabled(viewModel.aiSelectedConversationID.isEmpty)
            }

            if !viewModel.aiChatMessages.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(viewModel.aiChatMessages) { message in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(message.role == "user" ? "我" : "AI")
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(.secondary)
                            Text(message.content)
                                .textSelection(.enabled)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        .padding(8)
                        .background(message.role == "user" ? Color.accentColor.opacity(0.08) : Color.secondary.opacity(0.08))
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                    }
                }
            }

            HStack {
                TextField("向当前项目文档库提问", text: $viewModel.aiQuestion, axis: .vertical)
                    .textFieldStyle(.roundedBorder)
                    .lineLimit(2...4)
                    .onSubmit {
                        Task { await viewModel.askAIQuestion() }
                    }
                Button {
                    Task { await viewModel.askAIQuestion() }
                } label: {
                    Label("提问", systemImage: "paperplane")
                }
                .disabled(viewModel.isAIWorking || viewModel.aiSelectedGroupID.isEmpty)
            }

            if !viewModel.aiAnswerSources.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    Text("来源")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.secondary)
                    ForEach(viewModel.aiAnswerSources) { source in
                        AISourceRow(source: source)
                    }
                }
            }
        }
    }

    private var fileSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                TextField("搜索相关文件", text: $viewModel.aiFileSearchQuery)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit {
                        Task { await viewModel.searchAIFiles() }
                    }
                TextField("扩展名", text: $viewModel.aiFileSearchExtension)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 90)
                Button {
                    Task { await viewModel.searchAIFiles() }
                } label: {
                    Label("搜索", systemImage: "magnifyingglass")
                }
                .disabled(viewModel.isAIWorking)
                Button {
                    Task { await viewModel.summarizeAISelectedFile() }
                } label: {
                    Label("摘要文件", systemImage: "doc.text.magnifyingglass")
                }
                .disabled(viewModel.aiSelectedFile == nil || viewModel.isAIWorking)
            }

            if !viewModel.aiFileSearchResults.isEmpty {
                List(viewModel.aiFileSearchResults) { result in
                    Button {
                        viewModel.aiSelectedFile = result
                    } label: {
                        HStack {
                            Image(systemName: viewModel.aiSelectedFile?.id == result.id ? "checkmark.circle.fill" : "doc.text")
                                .foregroundStyle(.secondary)
                                .frame(width: 20)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(result.fileName)
                                Text(result.relativePath)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(1)
                            }
                            Spacer()
                            Text(ByteCountFormatter.string(fromByteCount: Int64(result.size), countStyle: .file))
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                    .buttonStyle(.plain)
                }
                .frame(minHeight: 140, maxHeight: 230)
                .listStyle(.inset)
            }
        }
    }

    private var contextPreviewSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("将发送给模型的内容", systemImage: "lock.doc")
                .font(.subheadline.weight(.semibold))
            Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 6) {
                GridRow {
                    Text("Provider").foregroundStyle(.secondary)
                    Text("\(viewModel.aiSettings.providerLabel) / \(viewModel.aiSettings.providerLocation)")
                }
                GridRow {
                    Text("项目说明").foregroundStyle(.secondary)
                    Text(viewModel.aiIncludeFileSnippets ? "索引 metadata + 受限文件片段" : "仅索引 metadata")
                }
                GridRow {
                    Text("文件摘要").foregroundStyle(.secondary)
                    Text(selectedFilePreview)
                }
                GridRow {
                    Text("文件上限").foregroundStyle(.secondary)
                    Text("\(viewModel.aiSettings.maxFileBytes / 1024) KB")
                }
            }
            .font(.callout)
            if !viewModel.aiErrorMessage.isEmpty {
                Text(viewModel.aiErrorMessage)
                    .foregroundStyle(.red)
                    .textSelection(.enabled)
            }
        }
        .padding(.top, 4)
    }

    private var selectedFilePreview: String {
        guard let file = viewModel.aiSelectedFile else {
            return "未选择文件，不会发送文件全文"
        }
        let size = ByteCountFormatter.string(fromByteCount: Int64(file.size), countStyle: .file)
        let truncated = file.size > viewModel.aiSettings.maxFileBytes ? "，会按上限截断" : ""
        return "\(file.relativePath) (\(size))\(truncated)"
    }

    private var resultSection: some View {
        Group {
            if !viewModel.aiSummary.isEmpty {
                ScrollView {
                    Text(viewModel.aiSummary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .textSelection(.enabled)
                        .padding(10)
                }
                .frame(minHeight: 120)
                .background(Color.secondary.opacity(0.08))
                .clipShape(RoundedRectangle(cornerRadius: 8))
            }
        }
    }
}

private struct AISourceRow: View {
    @EnvironmentObject private var viewModel: LauncherViewModel
    let source: AIRAGSource

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(source.displaySourceIndex.isEmpty ? source.relativePath : "[\(source.displaySourceIndex)] \(source.relativePath)")
                    .font(.callout.weight(.medium))
                    .lineLimit(1)
                Text("L\(source.lineStart)-\(source.lineEnd)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer()
                Button {
                    viewModel.openAISource(source)
                } label: {
                    Image(systemName: "doc")
                }
                .buttonStyle(.borderless)
                Button {
                    viewModel.revealAISource(source)
                } label: {
                    Image(systemName: "folder")
                }
                .buttonStyle(.borderless)
                Button {
                    viewModel.copyAISourcePath(source)
                } label: {
                    Image(systemName: "doc.on.doc")
                }
                .buttonStyle(.borderless)
                Button {
                    viewModel.copyAISourceCitation(source)
                } label: {
                    Image(systemName: "quote.bubble")
                }
                .buttonStyle(.borderless)
            }
            if !source.snippet.isEmpty {
                Text(source.snippet)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
                    .textSelection(.enabled)
            }
        }
        .padding(8)
        .background(Color.secondary.opacity(0.07))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

private struct AIDocumentSourceRow: View {
    @EnvironmentObject private var viewModel: LauncherViewModel
    let source: AIDocumentSource
    let onDelete: (AIDocumentSource) -> Void

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: source.contentStatus == "deleted_local" ? "doc.badge.minus" : "doc.text")
                .foregroundStyle(statusColor)
                .frame(width: 20)
            VStack(alignment: .leading, spacing: 3) {
                HStack {
                    Text(source.fileName.isEmpty ? source.relativePath : source.fileName)
                        .font(.callout.weight(.medium))
                        .lineLimit(1)
                    Text(source.statusLabel)
                        .font(.caption)
                        .foregroundStyle(statusColor)
                }
                Text(source.relativePath)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .textSelection(.enabled)
                Text("\(source.chunkCount) chunks  \(formatBytes(source.size))  \(formatDate(source.updatedAt))")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Button {
                viewModel.openAIDocumentSource(source)
            } label: {
                Image(systemName: "doc")
            }
            .buttonStyle(.borderless)
            .disabled(source.absolutePath.isEmpty || source.contentStatus == "deleted_local")
            Button {
                viewModel.revealAIDocumentSource(source)
            } label: {
                Image(systemName: "folder")
            }
            .buttonStyle(.borderless)
            .disabled(source.absolutePath.isEmpty || source.contentStatus == "deleted_local")
            Button {
                viewModel.copyAIDocumentSourcePath(source)
            } label: {
                Image(systemName: "doc.on.doc")
            }
            .buttonStyle(.borderless)
            if source.contentStatus == "deleted_local" {
                Button {
                    Task { await viewModel.restoreAIDocumentSource(source) }
                } label: {
                    Label("恢复", systemImage: "arrow.uturn.backward")
                }
                .disabled(viewModel.isAIWorking)
            } else {
                Button(role: .destructive) {
                    onDelete(source)
                } label: {
                    Label("删除索引", systemImage: "trash")
                }
                .disabled(viewModel.isAIWorking)
            }
        }
        .padding(.vertical, 4)
    }

    private var statusColor: Color {
        switch source.contentStatus {
        case "deleted_local": return .secondary
        case "error", "missing": return .red
        case "stale", "pending": return .orange
        default: return .secondary
        }
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
