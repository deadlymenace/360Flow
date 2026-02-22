import SwiftUI

struct CaptureView: View {
    @ObservedObject var viewModel: CaptureViewModel
    let onReview: () -> Void

    var body: some View {
        ZStack {
            CameraPreview(session: viewModel.cameraService.session)
                .ignoresSafeArea()

            RotationGuideOverlay(viewModel: viewModel)
                .padding(24)

            VStack {
                Text("\(viewModel.capturedCount) / 16")
                    .font(.headline)
                    .foregroundStyle(.white)
                    .padding(8)
                    .background(.black.opacity(0.45), in: Capsule())
                Spacer()

                if viewModel.isComplete {
                    Button("Review") { onReview() }
                        .buttonStyle(.borderedProminent)
                }

                Button(action: viewModel.captureCurrentSegment) {
                    Circle().fill(Color.white).frame(width: 72, height: 72)
                }
                .padding(.bottom, 32)
            }
        }
        .onAppear { viewModel.start() }
        .onDisappear { viewModel.stop() }
    }
}

struct RotationGuideOverlay: View {
    @ObservedObject var viewModel: CaptureViewModel

    var body: some View {
        GeometryReader { geo in
            Canvas { context, size in
                let center = CGPoint(x: size.width / 2, y: size.height / 2)
                let radius = min(size.width, size.height) / 2.4
                let lineWidth: CGFloat = 16

                for idx in 0..<MotionService.segmentCount {
                    let start = Angle.degrees(Double(idx) * MotionService.segmentAngle - 90 - viewModel.currentYaw)
                    let end = Angle.degrees(Double(idx + 1) * MotionService.segmentAngle - 90 - viewModel.currentYaw)
                    var path = Path()
                    path.addArc(center: center, radius: radius, startAngle: start, endAngle: end, clockwise: false)
                    let color: Color = viewModel.capturedIndices.contains(idx) ? .green : .gray.opacity(0.6)
                    context.stroke(path, with: .color(color), style: StrokeStyle(lineWidth: lineWidth, lineCap: .butt))
                }
            }
            .overlay {
                Circle()
                    .stroke(.yellow.opacity(0.9), lineWidth: 4)
                    .frame(width: min(geo.size.width, geo.size.height) / 1.2)
                    .scaleEffect(viewModel.isComplete ? 1 : 1.04)
                    .animation(.easeInOut(duration: 0.8).repeatForever(autoreverses: true), value: viewModel.currentSegmentIndex)
            }
        }
    }
}
