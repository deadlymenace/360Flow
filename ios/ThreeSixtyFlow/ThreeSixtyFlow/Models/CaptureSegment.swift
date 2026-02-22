import Foundation
import UIKit

struct CaptureSegment: Identifiable {
    let id: Int
    let targetYaw: Double
    var image: UIImage?
    var actualYaw: Double?
    var capturedAt: Date?

    var isCaptured: Bool { image != nil }
}
