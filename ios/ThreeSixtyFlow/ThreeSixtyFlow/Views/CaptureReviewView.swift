import SwiftUI

struct CaptureReviewView: View {
    @ObservedObject var captureViewModel: CaptureViewModel
    let onUpload: () -> Void

    private let columns = Array(repeating: GridItem(.flexible(), spacing: 8), count: 4)

    var body: some View {
        VStack {
            ScrollView {
                LazyVGrid(columns: columns, spacing: 8) {
                    ForEach(captureViewModel.segments) { segment in
                        Button {
                            captureViewModel.retake(segment: segment.id)
                        } label: {
                            ZStack {
                                Rectangle().fill(Color.gray.opacity(0.2))
                                if let image = segment.image {
                                    Image(uiImage: image)
                                        .resizable()
                                        .scaledToFill()
                                } else {
                                    Text("\(segment.id + 1)")
                                }
                            }
                            .frame(height: 88)
                            .clipped()
                        }
                    }
                }
                .padding()
            }

            Button("Upload & Stitch") { onUpload() }
                .buttonStyle(.borderedProminent)
                .disabled(!captureViewModel.isComplete)
                .padding()
        }
        .navigationTitle("Review")
    }
}
