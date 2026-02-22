import SwiftUI

struct CaptureView: View {
    @StateObject private var viewModel = CaptureViewModel()
    @Binding var navigateToReview: Bool
    @Binding var captureVM: CaptureViewModel?

    var body: some View {
        ZStack {
            // Camera preview
            CameraPreview(session: viewModel.cameraService.session)
                .ignoresSafeArea()

            // Rotation guide overlay
            VStack {
                Spacer()

                // Segment ring
                RotationGuide(
                    segments: viewModel.segments,
                    currentYaw: viewModel.currentYaw,
                    currentSegmentIndex: viewModel.currentSegmentIndex,
                    capturedIndices: viewModel.capturedIndices
                )
                .frame(width: 240, height: 240)
                .padding(.bottom, 20)

                // Capture count
                Text("\(viewModel.capturedCount) / \(CaptureSegment.count)")
                    .font(.title2.bold())
                    .foregroundColor(.white)
                    .padding(.horizontal, 16)
                    .padding(.vertical, 8)
                    .background(.ultraThinMaterial, in: Capsule())

                Spacer().frame(height: 24)

                // Capture button or review button
                if viewModel.isComplete {
                    Button {
                        captureVM = viewModel
                        navigateToReview = true
                    } label: {
                        Text("Review Photos")
                            .font(.headline)
                            .foregroundColor(.black)
                            .padding(.horizontal, 32)
                            .padding(.vertical, 14)
                            .background(.white, in: Capsule())
                    }
                } else {
                    Button {
                        viewModel.captureCurrentSegment()
                    } label: {
                        CaptureButton(
                            isAligned: viewModel.isAligned,
                            isCapturing: viewModel.isCapturing
                        )
                    }
                    .disabled(viewModel.isCapturing)
                }

                Spacer().frame(height: 40)
            }
        }
        .onAppear { viewModel.start() }
        .onDisappear { viewModel.stop() }
        .statusBarHidden()
    }
}

// MARK: - Capture Button

struct CaptureButton: View {
    let isAligned: Bool
    let isCapturing: Bool

    var body: some View {
        ZStack {
            Circle()
                .stroke(isAligned ? Color.green : Color.white.opacity(0.5), lineWidth: 4)
                .frame(width: 76, height: 76)

            Circle()
                .fill(isCapturing ? Color.gray : Color.white)
                .frame(width: 64, height: 64)
        }
    }
}

// MARK: - Rotation Guide Ring

struct RotationGuide: View {
    let segments: [CaptureSegment]
    let currentYaw: Double
    let currentSegmentIndex: Int
    let capturedIndices: Set<Int>

    var body: some View {
        ZStack {
            // Draw 16 arc segments
            ForEach(0..<CaptureSegment.count, id: \.self) { i in
                let startAngle = Double(i) * CaptureSegment.segmentAngle - 90 // -90 to start from top
                let endAngle = startAngle + CaptureSegment.segmentAngle - 1 // 1° gap

                ArcSegment(
                    startAngle: .degrees(startAngle),
                    endAngle: .degrees(endAngle),
                    isCaptured: capturedIndices.contains(i),
                    isCurrent: i == currentSegmentIndex
                )
            }

            // Yaw direction indicator
            let indicatorAngle = currentYaw - 90 // offset to match arc coordinate system
            Circle()
                .fill(Color.white)
                .frame(width: 10, height: 10)
                .offset(y: -105)
                .rotationEffect(.degrees(indicatorAngle))

            // Center count
            VStack(spacing: 2) {
                Text("\(capturedIndices.count)")
                    .font(.system(size: 36, weight: .bold))
                    .foregroundColor(.white)
                Text("of \(CaptureSegment.count)")
                    .font(.caption)
                    .foregroundColor(.white.opacity(0.7))
            }
        }
    }
}

struct ArcSegment: View {
    let startAngle: Angle
    let endAngle: Angle
    let isCaptured: Bool
    let isCurrent: Bool

    var body: some View {
        Path { path in
            path.addArc(
                center: CGPoint(x: 120, y: 120),
                radius: 100,
                startAngle: startAngle,
                endAngle: endAngle,
                clockwise: false
            )
        }
        .stroke(
            isCaptured ? Color.green : (isCurrent ? Color.yellow : Color.white.opacity(0.3)),
            style: StrokeStyle(lineWidth: isCurrent ? 10 : 6, lineCap: .round)
        )
        .animation(.easeInOut(duration: 0.2), value: isCaptured)
        .animation(.easeInOut(duration: 0.15), value: isCurrent)
    }
}
