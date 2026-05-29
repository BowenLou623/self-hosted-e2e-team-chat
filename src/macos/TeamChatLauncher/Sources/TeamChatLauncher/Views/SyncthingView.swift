import AppKit
import SwiftUI

struct SyncthingView: View {
    @EnvironmentObject private var viewModel: LauncherViewModel
    @State private var showAPIKey = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                apiSection
                statusSection
                deviceSection
            }
            .padding()
        }
    }

    private var apiSection: some View {
        GroupBox("Syncthing API") {
            VStack(alignment: .leading, spacing: 12) {
                Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 10) {
                    GridRow {
                        Text("API URL").foregroundStyle(.secondary)
                        TextField("http://127.0.0.1:8384", text: $viewModel.syncthingSettings.baseUrl)
                            .textFieldStyle(.roundedBorder)
                    }
                    GridRow {
                        Text("API Key").foregroundStyle(.secondary)
                        HStack {
                            if showAPIKey {
                                TextField("留空则使用已保存的 API Key", text: $viewModel.syncthingAPIKeyInput)
                                    .textFieldStyle(.roundedBorder)
                            } else {
                                SecureField("留空则使用已保存的 API Key", text: $viewModel.syncthingAPIKeyInput)
                                    .textFieldStyle(.roundedBorder)
                            }
                            Button {
                                showAPIKey.toggle()
                            } label: {
                                Label(showAPIKey ? "隐藏" : "显示", systemImage: showAPIKey ? "eye.slash" : "eye")
                            }
                            .labelStyle(.iconOnly)
                            .help(showAPIKey ? "隐藏 API Key" : "显示 API Key")
                        }
                    }
                    GridRow {
                        Text("超时").foregroundStyle(.secondary)
                        Stepper(
                            value: $viewModel.syncthingSettings.timeoutSeconds,
                            in: 0.5...30,
                            step: 0.5
                        ) {
                            Text("\(viewModel.syncthingSettings.timeoutSeconds, specifier: "%.1f") 秒")
                        }
                    }
                }

                howToGetKey

                HStack {
                    Button {
                        Task { await viewModel.saveSyncthingSettings() }
                    } label: {
                        Label("保存", systemImage: "square.and.arrow.down")
                    }
                    Button {
                        Task { await viewModel.testSyncthingConnection() }
                    } label: {
                        Label("测试连接", systemImage: "network")
                    }
                    .disabled(viewModel.isSyncthingTesting)
                    Button {
                        viewModel.openSyncthingWebUI()
                    } label: {
                        Label("打开 Web UI", systemImage: "safari")
                    }
                    if viewModel.isSyncthingTesting {
                        ProgressView()
                            .controlSize(.small)
                    }
                }
            }
            .padding(.top, 4)
        }
    }

    private var howToGetKey: some View {
        VStack(alignment: .leading, spacing: 4) {
            Label("如何获取 API Key", systemImage: "key")
                .font(.subheadline.weight(.semibold))
            Text("打开 Syncthing Web UI，进入 Actions / Settings / GUI，复制 API Key。")
                .foregroundStyle(.secondary)
                .textSelection(.enabled)
        }
        .font(.callout)
    }

    private var statusSection: some View {
        GroupBox("连接状态") {
            VStack(alignment: .leading, spacing: 10) {
                statusBadge(viewModel.syncthingTestStatus ?? viewModel.syncthingStatus)
                Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 8) {
                    GridRow {
                        Text("API Key").foregroundStyle(.secondary)
                        Text(savedKeyLabel)
                    }
                    GridRow {
                        Text("版本").foregroundStyle(.secondary)
                        Text((viewModel.syncthingStatus?.version?.isEmpty == false ? viewModel.syncthingStatus?.version : nil) ?? "-")
                    }
                    GridRow {
                        Text("URL").foregroundStyle(.secondary)
                        Text(viewModel.syncthingSettings.baseUrl.isEmpty ? SyncthingSettings.fallback.baseUrl : viewModel.syncthingSettings.baseUrl)
                            .textSelection(.enabled)
                    }
                }
                if !viewModel.syncthingTestMessage.isEmpty {
                    Text(viewModel.syncthingTestMessage)
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                }
                if let error = (viewModel.syncthingTestStatus ?? viewModel.syncthingStatus)?.error, !error.isEmpty {
                    Text(error)
                        .foregroundStyle(.red)
                        .textSelection(.enabled)
                }
                if let hint = (viewModel.syncthingTestStatus ?? viewModel.syncthingStatus)?.repairHint, !hint.isEmpty {
                    Text(hint)
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                }
            }
            .padding(.top, 4)
        }
    }

    private var savedKeyLabel: String {
        if !viewModel.syncthingAPIKeyInput.isEmpty {
            return "待保存/测试的新 Key"
        }
        return viewModel.syncthingSettings.apiKey.isEmpty ? "未配置" : "已配置"
    }

    private func statusBadge(_ status: SyncthingStatus?) -> some View {
        let label = status?.label ?? "未配置"
        let state = status?.state ?? "api_unconfigured"
        let color: Color = {
            switch state {
            case "connected": return .green
            case "api_unconfigured": return .orange
            case "api_key_error", "csrf_error", "not_running", "connection_failed", "not_installed":
                return .red
            default:
                return .secondary
            }
        }()
        return Label(label, systemImage: state == "connected" ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
            .foregroundStyle(color)
            .font(.headline)
    }

    private var deviceSection: some View {
        GroupBox("本机 Device ID") {
            VStack(alignment: .leading, spacing: 10) {
                Text(viewModel.syncthingStatus?.deviceId?.isEmpty == false ? viewModel.syncthingStatus?.deviceId ?? "" : "尚未读取到 Device ID")
                    .font(.system(.body, design: .monospaced))
                    .textSelection(.enabled)
                    .lineLimit(nil)
                HStack {
                    Button {
                        viewModel.copySyncthingDeviceID()
                    } label: {
                        Label("复制 Device ID", systemImage: "doc.on.doc")
                    }
                    .disabled(viewModel.syncthingStatus?.deviceId?.isEmpty ?? true)
                    Button {
                        Task { await viewModel.detectSyncthing() }
                    } label: {
                        Label("刷新 Device ID", systemImage: "arrow.clockwise")
                    }
                }
            }
            .padding(.top, 4)
        }
    }
}
