import AppKit
import SwiftUI

struct ProfilesView: View {
    @EnvironmentObject private var viewModel: LauncherViewModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                globalSettings
                profilePicker
                loginPanel
                createPanel
            }
            .padding()
        }
    }

    private var globalSettings: some View {
        GroupBox("本机路径") {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    TextField("Python 项目路径", text: $viewModel.config.projectRoot)
                    Button {
                        chooseProjectRoot()
                    } label: {
                        Label("选择", systemImage: "folder")
                    }
                }
                HStack {
                    TextField("Python 可执行文件", text: $viewModel.config.pythonExecutable)
                    Button {
                        viewModel.saveGlobalConfig()
                    } label: {
                        Label("保存", systemImage: "square.and.arrow.down")
                    }
                    Button {
                        viewModel.config.pythonExecutable = PythonInterpreterResolver.preferredExecutable()
                        viewModel.saveGlobalConfig()
                    } label: {
                        Label("自动选择", systemImage: "wand.and.stars")
                    }
                }
                HStack {
                    TextField("本地 venv 路径", text: $viewModel.config.venvPath)
                    Button {
                        viewModel.saveGlobalConfig()
                    } label: {
                        Label("保存 venv", systemImage: "square.and.arrow.down")
                    }
                }
                HStack {
                    Label(viewModel.pythonStatus.summary, systemImage: viewModel.pythonStatus.canLaunchClient ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                        .foregroundStyle(viewModel.pythonStatus.canLaunchClient ? .green : .orange)
                    Button {
                        Task { await viewModel.validatePythonInterpreter() }
                    } label: {
                        Label("检测 Python", systemImage: "waveform.path.ecg")
                    }
                }
                Text(viewModel.config.projectRoot.isEmpty ? "未配置项目路径" : viewModel.config.projectRoot)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
                if !viewModel.pythonStatus.details.isEmpty && !viewModel.pythonStatus.canLaunchClient {
                    Text(viewModel.pythonStatus.details)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(6)
                        .textSelection(.enabled)
                }
            }
            .padding(.vertical, 4)
        }
    }

    private var profilePicker: some View {
        GroupBox("Profiles") {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    Button {
                        Task { await viewModel.refreshProfiles() }
                    } label: {
                        Label("刷新", systemImage: "arrow.clockwise")
                    }
                    Spacer()
                    Text("\(viewModel.profiles.count) 个 profile")
                        .foregroundStyle(.secondary)
                }

                List(selection: Binding(
                    get: { viewModel.selectedProfile?.id },
                    set: { id in
                        guard let id, let profile = viewModel.profiles.first(where: { $0.id == id }) else { return }
                        Task { await viewModel.selectProfile(profile) }
                    }
                )) {
                    ForEach(viewModel.profiles) { profile in
                        HStack(spacing: 12) {
                            Image(systemName: profile.hasIdentity ? "person.crop.circle.fill" : "person.crop.circle.badge.questionmark")
                                .foregroundStyle(.secondary)
                                .frame(width: 20)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(profile.profile)
                                    .font(.headline)
                                Text(profile.displayName.isEmpty ? profile.userId : "\(profile.displayName)  \(profile.userId)")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(1)
                            }
                            Spacer()
                            Text(profile.hasPassword ? "已设置密码" : "未设置密码")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        .tag(profile.id)
                    }
                }
                .frame(minHeight: 180)
            }
        }
    }

    private var loginPanel: some View {
        GroupBox("本地账号登录") {
            VStack(alignment: .leading, spacing: 10) {
                if let profile = viewModel.selectedProfile {
                    Text("\(profile.profile)  \(profile.userId.isEmpty ? "" : profile.userId)")
                        .foregroundStyle(.secondary)
                    HStack {
                        SecureField("本地密码", text: $viewModel.loginPassword)
                            .textFieldStyle(.roundedBorder)
                        Button {
                            Task { await viewModel.loginSelectedProfile() }
                        } label: {
                            Label("登录", systemImage: "key.fill")
                        }
                        .disabled(viewModel.loginPassword.isEmpty || !profile.hasIdentity)
                    }
                    if !viewModel.launchTicket.isEmpty {
                        Label("已解锁，启动客户端时将跳过 Python 登录框", systemImage: "checkmark.circle.fill")
                            .foregroundStyle(.green)
                    }
                } else {
                    Text("请选择或创建 profile。")
                        .foregroundStyle(.secondary)
                }
            }
            .padding(.vertical, 4)
        }
    }

    private var createPanel: some View {
        GroupBox("创建 Profile") {
            Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 10) {
                GridRow {
                    Text("Profile")
                    TextField("例如 chris", text: $viewModel.createProfileName)
                }
                GridRow {
                    Text("显示名称")
                    TextField("可选", text: $viewModel.createDisplayName)
                }
                GridRow {
                    Text("密码")
                    SecureField("本地密码", text: $viewModel.createPassword)
                }
                GridRow {
                    Text("确认密码")
                    SecureField("再次输入", text: $viewModel.createConfirmPassword)
                }
                GridRow {
                    Text("")
                    Button {
                        Task { await viewModel.createProfileAndLogin() }
                    } label: {
                        Label("创建并登录", systemImage: "plus.circle.fill")
                    }
                    .buttonStyle(.borderedProminent)
                }
            }
            .padding(.vertical, 4)
        }
    }

    private func chooseProjectRoot() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        if panel.runModal() == .OK, let url = panel.url {
            guard ProjectRootResolver.isValidProjectRoot(url.path) else {
                viewModel.statusMessage = "请选择包含 src/app/main.py 的项目根目录"
                return
            }
            viewModel.config.projectRoot = url.path
            viewModel.config.venvPath = url.appendingPathComponent(".venv").path
            viewModel.saveGlobalConfig()
            Task { await viewModel.refreshProfiles() }
        }
    }
}
