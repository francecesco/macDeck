// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "MacDeck",
    platforms: [.macOS(.v14)],
    targets: [
        // Logica pura: nessun AppKit, nessuna rete vera. E' qui che stanno
        // i test, ed e' il motivo per cui esistono due target invece di uno.
        .target(name: "MacDeckCore", path: "Sources/MacDeckCore"),
        .executableTarget(
            name: "MacDeck",
            dependencies: ["MacDeckCore"],
            path: "Sources/MacDeck"),
        .testTarget(
            name: "MacDeckCoreTests",
            dependencies: ["MacDeckCore"],
            path: "Tests/MacDeckCoreTests"),
    ]
)
