import AVFoundation
import SwiftUI
import UIKit

class CameraService: NSObject, ObservableObject {
    let session = AVCaptureSession()
    private let photoOutput = AVCapturePhotoOutput()
    private var currentDevice: AVCaptureDevice?
    private var photoContinuation: CheckedContinuation<UIImage?, Never>?

    @Published var isConfigured = false

    func configure() {
        session.beginConfiguration()
        session.sessionPreset = .photo

        // Prefer ultra-wide camera for wider field of view, fall back to wide
        let discovery = AVCaptureDevice.DiscoverySession(
            deviceTypes: [.builtInUltraWideCamera, .builtInWideAngleCamera],
            mediaType: .video,
            position: .back
        )

        guard let device = discovery.devices.first,
              let input = try? AVCaptureDeviceInput(device: device) else {
            session.commitConfiguration()
            return
        }

        if session.canAddInput(input) {
            session.addInput(input)
        }
        if session.canAddOutput(photoOutput) {
            session.addOutput(photoOutput)
        }

        currentDevice = device
        session.commitConfiguration()

        DispatchQueue.main.async {
            self.isConfigured = true
        }
    }

    func start() {
        DispatchQueue.global(qos: .userInitiated).async {
            self.session.startRunning()
        }
    }

    func stop() {
        session.stopRunning()
    }

    /// Capture a single photo and return the UIImage via async/await.
    func capturePhoto() async -> UIImage? {
        await withCheckedContinuation { continuation in
            self.photoContinuation = continuation
            let settings = AVCapturePhotoSettings()
            settings.flashMode = .off
            self.photoOutput.capturePhoto(with: settings, delegate: self)
        }
    }
}

extension CameraService: AVCapturePhotoCaptureDelegate {
    func photoOutput(
        _ output: AVCapturePhotoOutput,
        didFinishProcessingPhoto photo: AVCapturePhoto,
        error: Error?
    ) {
        var image: UIImage?
        if let data = photo.fileDataRepresentation() {
            image = UIImage(data: data)
        }
        photoContinuation?.resume(returning: image)
        photoContinuation = nil
    }
}

// MARK: - SwiftUI Camera Preview

struct CameraPreview: UIViewRepresentable {
    let session: AVCaptureSession

    func makeUIView(context: Context) -> UIView {
        let view = UIView(frame: .zero)
        let previewLayer = AVCaptureVideoPreviewLayer(session: session)
        previewLayer.videoGravity = .resizeAspectFill
        view.layer.addSublayer(previewLayer)
        context.coordinator.previewLayer = previewLayer
        return view
    }

    func updateUIView(_ uiView: UIView, context: Context) {
        context.coordinator.previewLayer?.frame = uiView.bounds
    }

    func makeCoordinator() -> Coordinator {
        Coordinator()
    }

    class Coordinator {
        var previewLayer: AVCaptureVideoPreviewLayer?
    }
}
