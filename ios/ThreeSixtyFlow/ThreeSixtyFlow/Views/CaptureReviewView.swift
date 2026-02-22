import SwiftUI

struct CaptureReviewView: View {
    @ObservedObject var viewModel: CaptureViewModel
    @Binding var navigateToProcessing: Bool
    @Environment(\.dismiss) private var dismiss

    private let columns = Array(repeating: GridItem(.flexible(), spacing: 8), count: 4)

    var body: some View {
        VStack(spacing: 0) {
            // Thumbnail grid
            ScrollView {
                LazyVGrid(columns: columns, spacing: 8) {
                    ForEach(viewModel.segments) { segment in
                        SegmentThumbnail(segment: segment) {
                            viewModel.retakeSegment(segment.id)
                            dismiss()
                        }
                    }
                }
                .padding()
            }

            Divider()

            // Bottom action area
            VStack(spacing: 12) {
                Text("\(viewModel.capturedCount) of \(CaptureSegment.count) segments captured")
                    .font(.subheadline)
                    .foregroundColor(.secondary)

                Button {
                    navigateToProcessing = true
                } label: {
                    Text("Upload & Stitch")
                        .font(.headline)
                        .foregroundColor(.white)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 14)
                        .background(viewModel.isComplete ? Color.blue : Color.gray, in: RoundedRectangle(cornerRadius: 12))
                }
                .disabled(!viewModel.isComplete)
            }
            .padding()
        }
        .navigationTitle("Review Captures")
        .navigationBarTitleDisplayMode(.inline)
    }
}

struct SegmentThumbnail: View {
    let segment: CaptureSegment
    let onRetake: () -> Void

    var body: some View {
        ZStack(alignment: .topTrailing) {
            if let image = segment.image {
                Image(uiImage: image)
                    .resizable()
                    .scaledToFill()
                    .frame(height: 80)
                    .clipped()
                    .cornerRadius(8)
                    .onTapGesture { onRetake() }
            } else {
                RoundedRectangle(cornerRadius: 8)
                    .fill(Color.gray.opacity(0.2))
                    .frame(height: 80)
                    .overlay {
                        Image(systemName: "camera")
                            .foregroundColor(.gray)
                    }
            }

            // Segment number badge
            Text("\(segment.id + 1)")
                .font(.caption2.bold())
                .foregroundColor(.white)
                .padding(4)
                .background(segment.isCaptured ? Color.green : Color.gray, in: Circle())
                .padding(4)
        }
    }
}
