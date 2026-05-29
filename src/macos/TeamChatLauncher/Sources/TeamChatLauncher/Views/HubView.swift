import SwiftUI

struct HubView: View {
    @EnvironmentObject private var viewModel: LauncherViewModel

    var body: some View {
        Form {
            Section("Hub 配置") {
                Picker("传输模式", selection: $viewModel.launcherSettings.transport) {
                    Text("network").tag("network")
                    Text("memory").tag("memory")
                }
                TextField("Hub 地址", text: $viewModel.launcherSettings.hubAddress)
                Picker("日志级别", selection: $viewModel.launcherSettings.logLevel) {
                    ForEach(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], id: \.self) { level in
                        Text(level).tag(level)
                    }
                }
                HStack {
                    Button {
                        Task { await viewModel.saveLauncherSettings() }
                    } label: {
                        Label("保存", systemImage: "square.and.arrow.down")
                    }
                    Button {
                        Task { await viewModel.probeHub() }
                    } label: {
                        Label("测试连接", systemImage: "antenna.radiowaves.left.and.right")
                    }
                    Text(viewModel.hubProbeMessage)
                        .foregroundStyle(.secondary)
                }
            }

            Section("局域网发现") {
                HStack {
                    Button {
                        Task { await viewModel.discoverHubs() }
                    } label: {
                        Label(viewModel.isDiscoveringHubs ? "发现中" : "发现 Hub", systemImage: "dot.radiowaves.left.and.right")
                    }
                    .disabled(viewModel.isDiscoveringHubs)

                    Text(viewModel.discoveredHubs.isEmpty ? "同一局域网 UDP broadcast，手动地址保底" : "\(viewModel.discoveredHubs.count) 个结果")
                        .foregroundStyle(.secondary)
                }
                Text("远端电脑不需要启动本机 Hub；启动聊天客户端前会自动发现并连接主机 Hub。")
                    .foregroundStyle(.secondary)

                ForEach(viewModel.discoveredHubs) { hub in
                    HStack {
                        VStack(alignment: .leading, spacing: 3) {
                            Text(hub.hubName.isEmpty ? "Team Chat Hub" : hub.hubName)
                                .font(.headline)
                            Text("\(hub.address)  temp:\(hub.tempFilePort)  \(hub.version)")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        if hub.address == viewModel.launcherSettings.hubAddress {
                            Label("当前", systemImage: "checkmark.circle.fill")
                                .foregroundStyle(.green)
                        } else {
                            Button {
                                Task { await viewModel.useDiscoveredHub(hub) }
                            } label: {
                                Label("使用", systemImage: "arrow.right.circle")
                            }
                        }
                    }
                }
            }

            Section("当前 Profile") {
                Text(viewModel.selectedProfile?.profile ?? "未选择")
                Text(viewModel.selectedProfile?.configDir ?? "-")
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
        }
        .formStyle(.grouped)
        .padding()
    }
}
