import AppKit
import SwiftUI

struct LogsView: View {
    @EnvironmentObject private var viewModel: LauncherViewModel
    @ObservedObject var processController: ChatProcessController
    @State private var filter = "all"

    private var filteredLogs: [LogEntry] {
        switch filter {
        case "stdout": return processController.logs.filter { $0.stream == "stdout" }
        case "stderr": return processController.logs.filter { $0.stream == "stderr" }
        case "event": return processController.logs.filter { $0.event != nil }
        case "fatal": return processController.logs.filter { $0.event?.type == "fatal_error" || $0.stream == "stderr" }
        default: return processController.logs
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                StatusMetric(
                    title: "状态",
                    value: processController.state.label,
                    systemImage: "terminal"
                )
                StatusMetric(
                    title: "退出码",
                    value: processController.exitCode.map(String.init) ?? "-",
                    systemImage: "return"
                )
                Spacer()
                Picker("筛选", selection: $filter) {
                    Text("全部").tag("all")
                    Text("stdout").tag("stdout")
                    Text("stderr").tag("stderr")
                    Text("事件").tag("event")
                    Text("错误").tag("fatal")
                }
                .pickerStyle(.segmented)
                .frame(width: 330)
                Button {
                    copyVisibleLogs()
                } label: {
                    Label("复制", systemImage: "doc.on.doc")
                }
                .disabled(filteredLogs.isEmpty)
                Button {
                    processController.clearLogs()
                } label: {
                    Label("清空", systemImage: "trash")
                }
            }

            if let error = processController.lastError {
                Text(error)
                    .foregroundStyle(.red)
                    .textSelection(.enabled)
            }

            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 4) {
                        ForEach(filteredLogs) { entry in
                            HStack(alignment: .top, spacing: 8) {
                                Text(entryLabel(entry))
                                    .font(.caption)
                                    .foregroundStyle(entry.stream == "stderr" ? .red : (entry.event == nil ? .secondary : .blue))
                                    .frame(width: 48, alignment: .leading)
                                Text(entry.text)
                                    .font(.system(.caption, design: .monospaced))
                                    .textSelection(.enabled)
                            }
                            .id(entry.id)
                        }
                    }
                    .padding(10)
                }
                .background(.quaternary.opacity(0.35))
                .clipShape(RoundedRectangle(cornerRadius: 6))
                .onChange(of: processController.logs.count) { _ in
                    if let last = filteredLogs.last {
                        proxy.scrollTo(last.id, anchor: .bottom)
                    }
                }
            }
        }
        .padding()
    }

    private func entryLabel(_ entry: LogEntry) -> String {
        if entry.event != nil {
            return "event"
        }
        return entry.stream
    }

    private func copyVisibleLogs() {
        let text = filteredLogs.map { "[\($0.stream)] \($0.text)" }.joined(separator: "\n")
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
        viewModel.statusMessage = "日志已复制"
    }
}
