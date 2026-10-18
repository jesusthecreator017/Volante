// swift-tools-version:6.0
// OsoKit: the math shared by the iPhone and Watch apps. Plain Swift; only the stroke models use Foundation (JSON, bundle resources).
import PackageDescription

let package = Package(
    name: "OsoKit",
    platforms: [.iOS(.v17), .watchOS(.v10), .macOS(.v14)],
    products: [.library(name: "OsoKit", targets: ["OsoKit"])],
    targets: [
        // Resources/*.trees.json are copies of models/strokes/ (apple/scripts/sync-models.sh); a test fails when they drift.
        .target(name: "OsoKit", resources: [.copy("Resources")]),
        .testTarget(name: "OsoKitTests", dependencies: ["OsoKit"]),
    ]
)
