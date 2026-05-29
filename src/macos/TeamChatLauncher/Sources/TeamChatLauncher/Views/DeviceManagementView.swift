import SwiftUI

struct DeviceManagementView: View {
    @EnvironmentObject private var viewModel: LauncherViewModel

    var body: some View {
        Form {
            Section("本机设备") {
                LabeledContent("Device ID", value: viewModel.deviceSummary.deviceId.isEmpty ? "-" : viewModel.deviceSummary.deviceId)
                LabeledContent("指纹", value: viewModel.deviceSummary.deviceFingerprint.isEmpty ? "-" : viewModel.deviceSummary.deviceFingerprint)
                TextField("设备名称", text: $viewModel.deviceNameDraft)
                HStack {
                    Button {
                        Task { await viewModel.saveDeviceName() }
                    } label: {
                        Label("保存名称", systemImage: "square.and.arrow.down")
                    }
                    Button {
                        Task { await viewModel.refreshDeviceSummary() }
                    } label: {
                        Label("刷新", systemImage: "arrow.clockwise")
                    }
                    Text(viewModel.deviceSummary.configPath)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
            }

            Section("在线设备提示") {
                Text("同一 user_id 可以在多个设备登录。Hub 在线目录会按 user_id + device_id 区分设备；直聊加密仍以当前选中的对端设备密钥为目标。")
                    .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .padding()
    }
}
