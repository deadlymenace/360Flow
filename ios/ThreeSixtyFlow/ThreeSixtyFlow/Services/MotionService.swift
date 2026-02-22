import CoreMotion
import Combine

class MotionService: ObservableObject {
    private let motionManager = CMMotionManager()
    private let queue = OperationQueue()

    /// Current yaw in degrees, 0-360 range.
    @Published var currentYaw: Double = 0

    /// Which of the 16 segments the device is currently pointing at.
    @Published var currentSegmentIndex: Int = 0

    /// How close (in degrees) the current yaw is to the nearest segment center.
    @Published var offsetFromSegmentCenter: Double = 0

    static let segmentCount = 16
    static let segmentAngle = 360.0 / Double(segmentCount) // 22.5°

    /// Tolerance in degrees for considering the device "aligned" to a segment.
    static let alignmentTolerance = 5.0

    var isAligned: Bool {
        abs(offsetFromSegmentCenter) <= Self.alignmentTolerance
    }

    func startTracking() {
        guard motionManager.isDeviceMotionAvailable else { return }
        motionManager.deviceMotionUpdateInterval = 1.0 / 30.0 // 30 Hz

        motionManager.startDeviceMotionUpdates(
            using: .xArbitraryZVertical,
            to: queue
        ) { [weak self] motion, error in
            guard let motion = motion, let self = self else { return }

            // attitude.yaw is in radians, range -π to π
            let yawDeg = motion.attitude.yaw * 180.0 / .pi

            // Normalize to 0-360
            let normalized = (yawDeg + 360).truncatingRemainder(dividingBy: 360)

            let segmentIndex = Int(normalized / Self.segmentAngle) % Self.segmentCount
            let segmentCenter = Double(segmentIndex) * Self.segmentAngle + Self.segmentAngle / 2.0
            var offset = normalized - segmentCenter
            // Normalize offset to -180...180
            if offset > 180 { offset -= 360 }
            if offset < -180 { offset += 360 }

            DispatchQueue.main.async {
                self.currentYaw = normalized
                self.currentSegmentIndex = segmentIndex
                self.offsetFromSegmentCenter = offset
            }
        }
    }

    func stopTracking() {
        motionManager.stopDeviceMotionUpdates()
    }
}
