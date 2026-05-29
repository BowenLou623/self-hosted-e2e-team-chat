import SwiftUI

struct AdminDangerView: View {
    @EnvironmentObject private var viewModel: LauncherViewModel

    var body: some View {
        Form {
            Section("本机 Hub 状态") {
                HStack {
                    Button {
                        Task { await viewModel.refreshHubAdminStatus() }
                    } label: {
                        Label("刷新状态", systemImage: "arrow.clockwise")
                    }
                    Text(viewModel.hubAdminStatus.localHubRunning ? "当前 Hub 主机" : "非 Hub 主机")
                        .foregroundStyle(viewModel.hubAdminStatus.localHubRunning ? .green : .orange)
                }
                LabeledContent("Hub 运行", value: viewModel.hubAdminStatus.localHubRunning ? "是" : "否")
                LabeledContent("Admin 账号", value: viewModel.hubAdminStatus.adminUsername.isEmpty ? "admin" : viewModel.hubAdminStatus.adminUsername)
                LabeledContent("Admin 初始化", value: viewModel.hubAdminStatus.status.adminInitialized ? "是" : "否")
                LabeledContent("Hub 目录", value: viewModel.hubAdminStatus.status.hubDir.isEmpty ? "-" : viewModel.hubAdminStatus.status.hubDir)
                if let runtime = viewModel.hubAdminStatus.hubRuntime {
                    LabeledContent("Hub PID", value: runtime.pid.map(String.init) ?? "-")
                    LabeledContent("Hub 地址", value: "\(runtime.host ?? "-"):\(runtime.port ?? 0)")
                }
                if !viewModel.hubAdminStatus.deniedReason.isEmpty {
                    Text(viewModel.hubAdminStatus.deniedReason)
                        .foregroundStyle(.orange)
                }
            }

            Section(viewModel.hubAdminStatus.status.adminInitialized ? "Admin 登录" : "Admin 注册") {
                if viewModel.hubAdminStatus.status.adminInitialized {
                    adminSecureField("admin 密码", text: $viewModel.hubAdminLoginPassword)
                    Button {
                        Task { await viewModel.loginHubAdmin() }
                    } label: {
                        Label("登录 admin", systemImage: "person.badge.key")
                    }
                    .disabled(!viewModel.hubAdminStatus.localHubRunning)
                } else {
                    adminSecureField("admin 密码", text: $viewModel.hubAdminRegisterPassword)
                    adminSecureField("确认密码", text: $viewModel.hubAdminRegisterConfirm)
                    Button {
                        Task { await viewModel.registerHubAdmin() }
                    } label: {
                        Label("注册 admin", systemImage: "person.badge.plus")
                    }
                    .disabled(!viewModel.hubAdminStatus.localHubRunning)
                }
                if !viewModel.hubAdminAuthMessage.isEmpty {
                    Text(viewModel.hubAdminAuthMessage)
                        .foregroundStyle(.secondary)
                }
                HStack {
                    SecureField("Admin token", text: $viewModel.hubAdminTokenInput)
                    Button {
                        Task { await viewModel.refreshHubAdminStatus() }
                    } label: {
                        Label("验证状态", systemImage: "lock.shield")
                    }
                    Text(viewModel.hubAdminStatus.authenticated ? "已验证" : "未验证")
                        .foregroundStyle(viewModel.hubAdminStatus.authenticated ? .green : .secondary)
                }
            }

            Section("Admin 状态") {
                LabeledContent("设备注册", value: "\(viewModel.hubAdminStatus.status.deviceCount)")
                LabeledContent("离线队列", value: "\(viewModel.hubAdminStatus.status.offlineQueueCount)")
            }

            Section("销毁 Hub 本地内容") {
                TextField("确认短语：DESTROY HUB", text: $viewModel.hubDestroyConfirm)
                Toggle("同时删除 Hub 日志", isOn: $viewModel.hubDestroyIncludeLogs)
                HStack {
                    Button {
                        Task { await viewModel.dryRunDestroyHub() }
                    } label: {
                        Label("Dry Run", systemImage: "doc.text.magnifyingglass")
                    }
                    .disabled(!viewModel.hubAdminStatus.localHubRunning || !viewModel.hubAdminStatus.authenticated)
                    Button(role: .destructive) {
                        Task { await viewModel.executeDestroyHub() }
                    } label: {
                        Label("执行销毁", systemImage: "trash")
                    }
                    .disabled(
                        !viewModel.hubAdminStatus.localHubRunning ||
                        !viewModel.hubAdminStatus.authenticated ||
                        viewModel.hubDestroyConfirm != "DESTROY HUB"
                    )
                }

                if let result = viewModel.hubDestroyResult {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(result.deleted ? "已删除" : "Dry-run 目标")
                            .font(.headline)
                        ForEach(result.targets, id: \.self) { target in
                            Text(target)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .lineLimit(1)
                        }
                    }
                }
            }

            Section("边界") {
                Text("Admin 注册、登录、销毁只在运行 Hub 的主机本机可用。销毁只影响 Hub 本地数据库、离线密文队列、临时密文文件和 manifest。")
                    .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .padding()
    }

    private func adminSecureField(_ title: String, text: Binding<String>) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title)
                .font(.caption)
                .foregroundStyle(.secondary)
            SecureField(title, text: text)
                .textFieldStyle(.roundedBorder)
                .frame(maxWidth: 360)
        }
    }
}
