import AppKit
import SwiftUI

@main
struct TeamChatLauncherApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var viewModel = LauncherViewModel()

    var body: some Scene {
        WindowGroup("Team Chat Launcher 工作台") {
            ContentView()
                .environmentObject(viewModel)
                .frame(minWidth: 980, minHeight: 660)
                .task {
                    viewModel.loadInitialState()
                }
        }
        .commands {
            CommandGroup(after: .appInfo) {
                Button("刷新状态") {
                    Task {
                        await viewModel.refreshProfiles()
                        await viewModel.reloadSelectedProfileState()
                    }
                }
                .keyboardShortcut("r", modifiers: [.command])
            }
        }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
    }
}
