// swift-tools-version: 5.9

import PackageDescription

let package = Package(
    name: "TeamChatLauncher",
    platforms: [
        .macOS(.v13)
    ],
    products: [
        .executable(name: "TeamChatLauncher", targets: ["TeamChatLauncher"])
    ],
    targets: [
        .executableTarget(
            name: "TeamChatLauncher",
            path: "Sources/TeamChatLauncher"
        ),
        .testTarget(
            name: "TeamChatLauncherTests",
            dependencies: ["TeamChatLauncher"],
            path: "Tests/TeamChatLauncherTests"
        )
    ]
)
