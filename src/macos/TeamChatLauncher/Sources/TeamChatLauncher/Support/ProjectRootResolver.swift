import Foundation

enum ProjectRootResolver {
    static func detectProjectRoot() -> String? {
        let fileManager = FileManager.default
        var candidates: [URL] = [
            URL(fileURLWithPath: fileManager.currentDirectoryPath),
            Bundle.main.bundleURL.deletingLastPathComponent()
        ]

        if let envRoot = ProcessInfo.processInfo.environment["IMT_PROJECT_ROOT"], !envRoot.isEmpty {
            candidates.insert(URL(fileURLWithPath: envRoot), at: 0)
        }

        for candidate in candidates {
            if let match = findProjectRoot(startingAt: candidate) {
                return match.path
            }
        }
        return nil
    }

    static func isValidProjectRoot(_ path: String) -> Bool {
        guard !path.isEmpty else { return false }
        let root = URL(fileURLWithPath: path)
        let fileManager = FileManager.default
        return fileManager.fileExists(atPath: root.appendingPathComponent("src/app/main.py").path)
            && fileManager.fileExists(atPath: root.appendingPathComponent("src/app/control.py").path)
            && fileManager.fileExists(atPath: root.appendingPathComponent("requirements.txt").path)
    }

    private static func findProjectRoot(startingAt url: URL) -> URL? {
        var current = url.standardizedFileURL
        while true {
            if isValidProjectRoot(current.path) {
                return current
            }
            let parent = current.deletingLastPathComponent()
            if parent.path == current.path {
                return nil
            }
            current = parent
        }
    }
}
