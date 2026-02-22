import SwiftUI
import Combine

@MainActor
class CaptureViewModel: ObservableObject {
    let cameraService = CameraService()
    let motionService = MotionService()

    @Published var segments: [CaptureSegment] = CaptureSegment.makeAll()
    @Published var capturedIndices: Set<Int> = []
    @Published var isCapturing = false
    @Published var lastCapturedIndex: Int?

    var capturedCount: Int { capturedIndices.count }
    var isComplete: Bool { capturedCount == CaptureSegment.count }

    var currentSegmentIndex: Int { motionService.currentSegmentIndex }
    var currentYaw: Double { motionService.currentYaw }
    var isAligned: Bool { motionService.isAligned }

    private var cancellables = Set<AnyCancellable>()

    func start() {
        cameraService.configure()
        cameraService.start()
        motionService.startTracking()

        // Forward motion updates to trigger SwiftUI refresh
        motionService.objectWillChange
            .receive(on: DispatchQueue.main)
            .sink { [weak self] _ in self?.objectWillChange.send() }
            .store(in: &cancellables)
    }

    func stop() {
        cameraService.stop()
        motionService.stopTracking()
        cancellables.removeAll()
    }

    func captureCurrentSegment() {
        let segmentIndex = currentSegmentIndex
        guard !isCapturing else { return }

        isCapturing = true

        Task {
            if let image = await cameraService.capturePhoto() {
                segments[segmentIndex].image = image
                segments[segmentIndex].actualYaw = currentYaw
                segments[segmentIndex].capturedAt = Date()
                capturedIndices.insert(segmentIndex)
                lastCapturedIndex = segmentIndex

                HapticManager.capture()

                if isComplete {
                    HapticManager.success()
                }
            }
            isCapturing = false
        }
    }

    func retakeSegment(_ index: Int) {
        segments[index].image = nil
        segments[index].actualYaw = nil
        segments[index].capturedAt = nil
        capturedIndices.remove(index)
    }

    /// Get all captured images in segment order.
    func orderedImages() -> [UIImage] {
        segments.compactMap { $0.image }
    }
}
