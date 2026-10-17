// swift-tools-version:6.0
// OsoKit: the math shared by the iPhone and Watch apps. Plain Swift (no Foundation), so `swift test` runs anywhere.
import PackageDescription

let package = Package(
    name: "OsoKit",
    platforms: [.iOS(.v17), .watchOS(.v10), .macOS(.v14)],
    products: [.library(name: "OsoKit", targets: ["OsoKit"])],
    targets: [
        .target(name: "OsoKit"),
        .testTarget(name: "OsoKitTests", dependencies: ["OsoKit"]),
    ]
)
