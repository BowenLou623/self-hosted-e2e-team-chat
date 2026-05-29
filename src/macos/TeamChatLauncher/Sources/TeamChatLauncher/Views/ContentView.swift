import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var viewModel: LauncherViewModel
    @SceneStorage("TeamChatLauncher.sidebarSelection") private var selectionRaw: String = SidebarItem.overview.rawValue

    private var selection: Binding<SidebarItem> {
        Binding(
            get: { SidebarItem(rawValue: selectionRaw) ?? .overview },
            set: { selectionRaw = $0.rawValue }
        )
    }

    var body: some View {
        Group {
            if viewModel.shouldShowOnboarding {
                OnboardingView()
            } else {
                NavigationSplitView {
                    List(selection: selection) {
                        ForEach(SidebarItem.allCases) { item in
                            Label(item.title, systemImage: item.systemImage)
                                .contentShape(Rectangle())
                                .onTapGesture {
                                    selectionRaw = item.rawValue
                                }
                                .tag(item)
                        }
                    }
                    .listStyle(.sidebar)
                } detail: {
                    detailView
                        .navigationTitle(selection.wrappedValue.title)
                        .toolbar {
                            LauncherToolbar(processController: viewModel.processController)
                        }
                }
            }
        }
    }

    @ViewBuilder
    private var detailView: some View {
        switch selection.wrappedValue {
        case .overview:
            OverviewView(processController: viewModel.processController)
        case .profiles:
            ProfilesView()
        case .install:
            InstallWizardView()
        case .environment:
            EnvironmentView()
        case .hub:
            HubView()
        case .devices:
            DeviceManagementView()
        case .admin:
            AdminDangerView()
        case .syncthing:
            SyncthingView()
        case .sync:
            ProjectSyncView()
        case .security:
            SecurityStatusView()
        case .logs:
            LogsView(processController: viewModel.processController)
        case .ai:
            AIPlaceholderView()
        }
    }
}

private struct LauncherToolbar: ToolbarContent {
    @EnvironmentObject private var viewModel: LauncherViewModel
    @ObservedObject var processController: ChatProcessController

    var body: some ToolbarContent {
        ToolbarItemGroup {
            Button {
                Task {
                    await viewModel.refreshProfiles()
                    await viewModel.reloadSelectedProfileState()
                }
            } label: {
                Label("刷新", systemImage: "arrow.clockwise")
            }

            Button {
                viewModel.startClient()
            } label: {
                Label(viewModel.isResolvingHubForLaunch ? "连接中" : "启动", systemImage: "play.fill")
            }
            .disabled(processController.isRunning || viewModel.isResolvingHubForLaunch || !viewModel.pythonStatus.canLaunchClient)

            Button {
                viewModel.stopClient()
            } label: {
                Label("停止", systemImage: "stop.fill")
            }
            .disabled(!processController.isRunning)
        }
    }
}
