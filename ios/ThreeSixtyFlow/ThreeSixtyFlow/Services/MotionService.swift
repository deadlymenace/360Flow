import Combine
import CoreMotion

class MotionService: ObservableObject {
    private let motionManager = CMMotionManager()
    private let queue = OperationQueue()

    @Published var currentYaw: Double = 0
    @Published var currentSegmentIndex: Int = 0

    static let segmentCount = 16
    static let segmentAngle = 360.0 / Double(segmentCount)

    func startTracking() {
        guard motionManager.isDeviceMotionAvailable else { return }
        motionManager.deviceMotionUpdateInterval = 1.0 / 30.0

        motionManager.startDeviceMotionUpdates(using: .xArbitraryZVertical, to: queue) { [weak self] motion, _ in
            guard let self, let motion else { return }
            let yawDeg = motion.attitude.yaw * 180.0 / .pi
            DispatchQueue.main.async {
                self.currentYaw = yawDeg
                let normalized = (yawDeg + 360).truncatingRemainder(dividingBy: 360)
                self.currentSegmentIndex = Int(normalized / Self.segmentAngle) % Self.segmentCount
            }
        }
    }

    func stopTracking() {
        motionManager.stopDeviceMotionUpdates()
    }
}
