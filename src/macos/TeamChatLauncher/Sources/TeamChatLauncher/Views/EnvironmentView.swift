import SwiftUI

struct EnvironmentView: View {
    @EnvironmentObject private var viewModel: LauncherViewModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                header
                checks
            }
            .padding()
        }
        .task {
            if viewModel.environmentReport == nil {
                await viewModel.checkEnvironment()
            }
        }
    }

    private var header: some View {
        HStack {
            VStack(alignment: .leading, spacing: 4) {
                Text(viewModel.environmentReport?.label ?? "未检测")
                    .font(.title3.weight(.semibold))
                Text(viewModel.environmentReport?.projectRoot ?? viewModel.config.projectRoot)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
                    .textSelection(.enabled)
            }
            Spacer()
            Button {
                Task { await viewModel.checkEnvironment() }
            } label: {
                Label("重新检测", systemImage: "arrow.clockwise")
            }
        }
    }

    private var checks: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(viewModel.environmentReport?.checks ?? []) { item in
                EnvironmentCheckRow(item: item)
                Divider()
            }
            if viewModel.environmentReport == nil {
                Text("尚未运行环境检查。")
                    .foregroundStyle(.secondary)
            }
        }
    }
}

private struct EnvironmentCheckRow: View {
    let item: EnvironmentCheckItem

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: item.systemImage)
                .foregroundStyle(color)
                .frame(width: 20)
            VStack(alignment: .leading, spacing: 3) {
                Text(item.title)
                    .font(.headline)
                Text(item.message)
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
                if !item.repairHint.isEmpty {
                    Text(item.repairHint)
                        .font(.caption)
                        .foregroundStyle(.orange)
                        .textSelection(.enabled)
                }
            }
            Spacer()
            Text(label)
                .font(.caption)
                .foregroundStyle(color)
        }
        .padding(.vertical, 4)
    }

    private var color: Color {
        switch item.status {
        case "ok": return .green
        case "error": return .red
        default: return .orange
        }
    }

    private var label: String {
        switch item.status {
        case "ok": return "OK"
        case "error": return "错误"
        default: return "提醒"
        }
    }
}
