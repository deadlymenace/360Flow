import AVFoundation
import Combine
import Foundation
import UIKit

@MainActor
class CaptureViewModel: NSObject, ObservableObject {
    @Published var segments: [CaptureSegment] = (0..<MotionService.segmentCount).map {
        CaptureSegment(id: $0, targetYaw: Double($0) * MotionService.segmentAngle, image: nil, actualYaw: nil, capturedAt: nil)
    }
    @Published var capturedIndices: Set<Int> = []
    @Published var currentSegmentIndex: Int = 0
    @Published var currentYaw: Double = 0

    let cameraService = CameraService()
    let motionService = MotionService()

    private var cancellables = Set<AnyCancellable>()
    private var pendingCaptureSegment: Int?

    var capturedCount: Int { capturedIndices.count }
    var isComplete: Bool { capturedCount == MotionService.segmentCount }

    override init() {
        super.init()
        bindMotion()
    }

    func start() {
        cameraService.configure()
        cameraService.start()
        motionService.startTracking()
    }

    func stop() {
        cameraService.stop()
        motionService.stopTracking()
    }

    func captureCurrentSegment() {
        capture(segment: currentSegmentIndex)
    }

    func capture(segment index: Int) {
        pendingCaptureSegment = index
        cameraService.capturePhoto(delegate: self)
    }

    func retake(segment index: Int) {
        segments[index].image = nil
        segments[index].actualYaw = nil
        segments[index].capturedAt = nil
        capturedIndices.remove(index)
    }

    private func bindMotion() {
        motionService.$currentYaw
            .receive(on: DispatchQueue.main)
            .sink { [weak self] yaw in
                self?.currentYaw = yaw
            }
            .store(in: &cancellables)

        motionService.$currentSegmentIndex
            .receive(on: DispatchQueue.main)
            .sink { [weak self] index in
                guard let self else { return }
                self.currentSegmentIndex = index
                self.handleAutoCapture(segment: index)
            }
            .store(in: &cancellables)
    }

    private func handleAutoCapture(segment: Int) {
        guard !capturedIndices.contains(segment), pendingCaptureSegment == nil else { return }
        let center = Double(segment) * MotionService.segmentAngle + MotionService.segmentAngle / 2
        let normalized = (currentYaw + 360).truncatingRemainder(dividingBy: 360)
        let delta = abs(normalized - center)
        let wrapped = min(delta, 360 - delta)
        if wrapped <= 5 {
            capture(segment: segment)
        }
    }

    private func store(image: UIImage) {
        guard let segment = pendingCaptureSegment else { return }
        segments[segment].image = image
        segments[segment].actualYaw = currentYaw
        segments[segment].capturedAt = Date()
        capturedIndices.insert(segment)
        HapticManager.capture()
        pendingCaptureSegment = nil
    }
}

extension CaptureViewModel: AVCapturePhotoCaptureDelegate {
    nonisolated func photoOutput(_ output: AVCapturePhotoOutput,
                                 didFinishProcessingPhoto photo: AVCapturePhoto,
                                 error: Error?) {
        guard error == nil,
              let data = photo.fileDataRepresentation(),
              let image = UIImage(data: data) else {
            return
        }

        Task { @MainActor in
            self.store(image: image)
        }
    }
}
