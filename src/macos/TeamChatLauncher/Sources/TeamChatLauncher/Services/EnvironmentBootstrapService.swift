import Foundation

final class EnvironmentBootstrapService {
    private let controlClient: ControlCLIClient

    init(controlClient: ControlCLIClient) {
        self.controlClient = controlClient
    }

    func verify(profile: String?, config: GlobalLauncherConfig) async throws -> EnvironmentBootstrapResult {
        try await controlClient.verifyEnvironment(
            profile: profile,
            venvPath: config.venvPath,
            config: config
        )
    }

    func bootstrap(profile: String, installDeps: Bool, verifyClient: Bool, config: GlobalLauncherConfig) async throws -> EnvironmentBootstrapResult {
        try await controlClient.bootstrapEnvironment(
            profile: profile,
            venvPath: config.venvPath,
            installDeps: installDeps,
            verifyClient: verifyClient,
            config: config
        )
    }

    func manualCommands(profile: String, config: GlobalLauncherConfig) async throws -> EnvironmentBootstrapResult {
        try await controlClient.manualEnvironmentCommands(
            profile: profile,
            venvPath: config.venvPath,
            config: config
        )
    }
}
