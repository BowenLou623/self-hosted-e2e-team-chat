import AppKit
import SwiftUI

struct OnboardingView: View {
    @EnvironmentObject private var viewModel: LauncherViewModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                HStack(alignment: .top, spacing: 20) {
                    VStack(alignment: .leading, spacing: 10) {
                        Text("Team Chat Launcher")
                            .font(.system(size: 38, weight: .bold))
                        Text("安装、配置、启动集中在一个本地工作台。你可以自动配置 Python 环境，也可以继续使用 Bash/Python 手动运行。")
                            .font(.title3)
                            .foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    Spacer()
                    VStack(alignment: .trailing, spacing: 10) {
                        StatusBadge(label: viewModel.bootstrapResult.label, status: viewModel.bootstrapResult.status)
                        Button {
                            viewModel.skipOnboarding()
                        } label: {
                            Label("跳过并进入 Launcher", systemImage: "forward.fill")
                        }
                    }
                }

                modePicker
                InstallWizardView(isOnboarding: true)
            }
            .padding(28)
            .frame(maxWidth: 1160)
        }
        .task {
            await viewModel.verifyEnvironmentBootstrap()
            await viewModel.loadManualEnvironmentCommands()
        }
    }

    private var modePicker: some View {
        LauncherCard(title: "选择配置模式", systemImage: "slider.horizontal.3") {
            Picker("配置模式", selection: Binding(
                get: { viewModel.config.installMode },
                set: { viewModel.setInstallMode($0) }
            )) {
                Text("自动配置").tag("automatic")
                Text("手动配置").tag("manual")
            }
            .pickerStyle(.segmented)
            Text(viewModel.config.installMode == "automatic"
                 ? "Launcher 会在确认后创建 venv、安装 requirements.txt，并验证客户端前置条件。"
                 : "Launcher 只提供可复制命令，不写入依赖或运行安装流程。")
                .foregroundStyle(.secondary)
        }
    }
}

struct InstallWizardView: View {
    @EnvironmentObject private var viewModel: LauncherViewModel
    var isOnboarding = false
    @State private var installDeps = true
    @State private var verifyClient = true
    @State private var showConfirmBootstrap = false

    var body: some View {
        Group {
            if isOnboarding {
                content
            } else {
                ScrollView {
                    content
                        .padding(18)
                }
            }
        }
        .task {
            if viewModel.bootstrapResult.steps.isEmpty {
                await viewModel.verifyEnvironmentBootstrap()
            }
            if viewModel.bootstrapResult.copyableCommands.isEmpty {
                await viewModel.loadManualEnvironmentCommands()
            }
        }
        .confirmationDialog(
            "确认自动配置 Python 环境",
            isPresented: $showConfirmBootstrap,
            titleVisibility: .visible
        ) {
            Button("创建 venv 并安装依赖") {
                Task {
                    await viewModel.runEnvironmentBootstrap(installDeps: installDeps, verifyClient: verifyClient)
                }
            }
            Button("取消", role: .cancel) {}
        } message: {
            Text("将使用 \(viewModel.config.pythonExecutable) 在 \(viewModel.config.venvPath) 创建或复用 venv。安装依赖会访问 Python package index。")
        }
    }

    private var content: some View {
        VStack(alignment: .leading, spacing: 18) {
            if !isOnboarding {
                header
            }
            summary
            if viewModel.config.installMode == "manual" {
                manualCommands
            } else {
                automaticControls
                steps
                logs
            }
        }
    }

    private var header: some View {
        HStack {
            VStack(alignment: .leading, spacing: 4) {
                Text("安装工作台")
                    .font(.title2.weight(.semibold))
                Text("面向首次启动和环境修复，保留手动 Bash/Python 模式。")
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Button {
                Task {
                    await viewModel.verifyEnvironmentBootstrap()
                    await viewModel.loadManualEnvironmentCommands()
                }
            } label: {
                Label("重新检测", systemImage: "arrow.clockwise")
            }
        }
    }

    private var summary: some View {
        LauncherCard(title: "当前安装状态", systemImage: "checklist.checked") {
            Grid(alignment: .leading, horizontalSpacing: 18, verticalSpacing: 10) {
                GridRow {
                    Text("模式").foregroundStyle(.secondary)
                    Picker("模式", selection: Binding(
                        get: { viewModel.config.installMode },
                        set: { viewModel.setInstallMode($0) }
                    )) {
                        Text("自动").tag("automatic")
                        Text("手动").tag("manual")
                    }
                    .pickerStyle(.segmented)
                    .frame(width: 220)
                }
                GridRow {
                    Text("项目根目录").foregroundStyle(.secondary)
                    Text(viewModel.config.projectRoot.isEmpty ? "未配置" : viewModel.config.projectRoot)
                        .lineLimit(2)
                        .textSelection(.enabled)
                }
                GridRow {
                    Text("Python").foregroundStyle(.secondary)
                    Text(viewModel.config.pythonExecutable)
                        .lineLimit(2)
                        .textSelection(.enabled)
                }
                GridRow {
                    Text("venv").foregroundStyle(.secondary)
                    Text(viewModel.config.venvPath.isEmpty ? "未配置" : viewModel.config.venvPath)
                        .lineLimit(2)
                        .textSelection(.enabled)
                }
            }
            HStack {
                Button {
                    choosePythonExecutable()
                } label: {
                    Label("选择 Python", systemImage: "chevron.left.forwardslash.chevron.right")
                }
                Button {
                    viewModel.openPythonDownloadPage()
                } label: {
                    Label("Python.org", systemImage: "safari")
                }
                Button {
                    viewModel.copyHomebrewPythonCommand()
                } label: {
                    Label("复制 brew 命令", systemImage: "doc.on.doc")
                }
            }
        }
    }

    private var automaticControls: some View {
        LauncherCard(title: "自动配置", systemImage: "wand.and.stars") {
            Toggle("安装 requirements.txt", isOn: $installDeps)
            Toggle("验证聊天客户端可启动前置条件", isOn: $verifyClient)
            HStack {
                Button {
                    Task { await viewModel.verifyEnvironmentBootstrap() }
                } label: {
                    Label("检查 Python", systemImage: "waveform.path.ecg")
                }
                Button {
                    showConfirmBootstrap = true
                } label: {
                    Label(viewModel.isBootstrapping ? "配置中" : "一键配置 / 修复", systemImage: "play.circle.fill")
                }
                .buttonStyle(.borderedProminent)
                .disabled(viewModel.isBootstrapping)
                if viewModel.isBootstrapping {
                    ProgressView()
                        .controlSize(.small)
                }
                Spacer()
                Button {
                    viewModel.finishOnboarding()
                } label: {
                    Label("完成并进入 Launcher", systemImage: "checkmark.circle")
                }
                .disabled(viewModel.bootstrapResult.status == "failed")
            }
            Text("自动模式只在确认后创建 venv 或安装依赖；不会静默安装系统级 Python、Syncthing、Hub 或 AI 模型。")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    private var steps: some View {
        LauncherCard(title: "安装步骤", systemImage: "list.bullet.rectangle") {
            VStack(spacing: 10) {
                ForEach(viewModel.bootstrapResult.steps) { step in
                    InstallStepRow(step: step)
                    if step.id != viewModel.bootstrapResult.steps.last?.id {
                        Divider()
                    }
                }
                if viewModel.bootstrapResult.steps.isEmpty {
                    Text("尚未运行安装检查。")
                        .foregroundStyle(.secondary)
                }
            }
        }
    }

    private var logs: some View {
        LauncherCard(title: "执行日志", systemImage: "terminal") {
            HStack {
                Text("\(viewModel.bootstrapResult.logs.count) 条日志")
                    .foregroundStyle(.secondary)
                Spacer()
                Button {
                    viewModel.copyBootstrapLogs()
                } label: {
                    Label("复制日志", systemImage: "doc.on.doc")
                }
                .disabled(viewModel.bootstrapResult.logs.isEmpty)
            }
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 8) {
                    ForEach(viewModel.bootstrapResult.logs) { entry in
                        VStack(alignment: .leading, spacing: 4) {
                            HStack {
                                StatusBadge(label: entry.level.uppercased(), status: entry.level == "error" ? "failed" : "done")
                                Text(entry.message)
                                    .font(.callout.weight(.semibold))
                            }
                            if !entry.command.isEmpty {
                                Text("$ \(entry.command)")
                                    .font(.system(.caption, design: .monospaced))
                                    .textSelection(.enabled)
                            }
                            if !entry.detail.isEmpty {
                                Text(entry.detail)
                                    .font(.system(.caption, design: .monospaced))
                                    .foregroundStyle(.secondary)
                                    .textSelection(.enabled)
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
                .padding(10)
            }
            .frame(minHeight: 160, maxHeight: 260)
            .background(.quaternary.opacity(0.25), in: RoundedRectangle(cornerRadius: 6))
        }
    }

    private var manualCommands: some View {
        LauncherCard(title: "手动 Bash / Python 命令", systemImage: "terminal.fill") {
            HStack {
                Text("按顺序复制执行，也可以继续使用脚本启动 Hub 和客户端。")
                    .foregroundStyle(.secondary)
                Spacer()
                Button {
                    Task { await viewModel.loadManualEnvironmentCommands() }
                } label: {
                    Label("刷新命令", systemImage: "arrow.clockwise")
                }
            }
            VStack(spacing: 10) {
                ForEach(viewModel.bootstrapResult.copyableCommands) { command in
                    HStack(alignment: .top, spacing: 12) {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(command.title)
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(.secondary)
                            Text(command.command)
                                .font(.system(.callout, design: .monospaced))
                                .textSelection(.enabled)
                        }
                        Spacer()
                        Button {
                            viewModel.copyCommand(command)
                        } label: {
                            Label("复制", systemImage: "doc.on.doc")
                        }
                        .labelStyle(.iconOnly)
                        .help("复制 \(command.title)")
                    }
                    Divider()
                }
            }
            Button {
                viewModel.finishOnboarding()
            } label: {
                Label("我会手动配置，进入 Launcher", systemImage: "checkmark.circle")
            }
        }
    }

    private func choosePythonExecutable() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.prompt = "选择"
        if panel.runModal() == .OK, let url = panel.url {
            viewModel.config.pythonExecutable = url.path
            viewModel.saveGlobalConfig()
        }
    }
}

struct InstallStepRow: View {
    let step: BootstrapStep

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: step.systemImage)
                .foregroundStyle(color)
                .frame(width: 22)
            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text(step.title)
                        .font(.headline)
                    StatusBadge(label: step.label, status: step.status)
                }
                Text(step.message)
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
                if !step.repairHint.isEmpty {
                    Text(step.repairHint)
                        .font(.caption)
                        .foregroundStyle(step.status == "failed" ? .red : .orange)
                        .textSelection(.enabled)
                }
            }
            Spacer()
        }
        .padding(.vertical, 2)
    }

    private var color: Color {
        switch step.status {
        case "done": return .green
        case "failed": return .red
        case "skippable": return .secondary
        default: return .orange
        }
    }
}
