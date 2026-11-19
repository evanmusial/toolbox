// swift-tools-version: 5.10
import PackageDescription

let package = Package(
    name: "Wavewing",
    platforms: [.macOS(.v13)],
    products: [.executable(name: "Wavewing", targets: ["Wavewing"])],
    targets: [
        .executableTarget(name: "Wavewing", path: "Sources/Wavewing"),
        .testTarget(name: "WavewingTests", dependencies: ["Wavewing"], path: "Tests/WavewingTests")
    ],
    swiftLanguageVersions: [.v5]
)
