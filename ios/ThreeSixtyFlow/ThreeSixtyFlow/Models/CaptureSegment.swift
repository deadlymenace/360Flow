import UIKit

struct CaptureSegment: Identifiable {
    let id: Int              // 0-15, the segment index
    let targetYaw: Double    // center angle in degrees for this segment (0, 22.5, 45, ...)

    var image: UIImage?
    var actualYaw: Double?
    var capturedAt: Date?

    var isCaptured: Bool { image != nil }

    static let count = 16
    static let segmentAngle = 360.0 / Double(count) // 22.5°

    static func makeAll() -> [CaptureSegment] {
        (0..<count).map { i in
            CaptureSegment(
                id: i,
                targetYaw: Double(i) * segmentAngle
            )
        }
    }
}
