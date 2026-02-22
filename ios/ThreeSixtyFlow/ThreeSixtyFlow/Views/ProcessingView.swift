import SwiftUI

struct ProcessingView: View {
    let projectId: String
    let images: [UIImage]

    @State private var phase: Phase = .uploading
    @State private var uploadProgress: Double = 0
    @State private var error: String?
    @State private var panoramaImage: UIImage?

    enum Phase {
        case uploading
        case stitching
        case done
        case failed
    }

    var body: some View {
        VStack(spacing: 24) {
            Spacer()

            switch phase {
            case .uploading:
                ProgressView(value: uploadProgress) {
                    Text("Uploading photos...")
                        .font(.headline)
                }
                .progressViewStyle(.linear)
                .padding(.horizontal, 40)

                Text("\(Int(uploadProgress * 100))%")
                    .font(.title2.monospacedDigit())
                    .foregroundColor(.secondary)

            case .stitching:
                ProgressView()
                    .scaleEffect(1.5)
                    .padding(.bottom, 8)

                Text("Stitching your panorama...")
                    .font(.headline)

                Text("This may take a moment")
                    .font(.subheadline)
                    .foregroundColor(.secondary)

            case .done:
                Image(systemName: "checkmark.circle.fill")
                    .font(.system(size: 64))
                    .foregroundColor(.green)

                Text("Panorama ready!")
                    .font(.headline)

            case .failed:
                Image(systemName: "exclamationmark.triangle.fill")
                    .font(.system(size: 64))
                    .foregroundColor(.red)

                Text("Stitching failed")
                    .font(.headline)

                if let error {
                    Text(error)
                        .font(.subheadline)
                        .foregroundColor(.secondary)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal)
                }

                Button("Retry") {
                    Task { await startProcessing() }
                }
                .buttonStyle(.borderedProminent)
            }

            Spacer()

            if phase == .done, panoramaImage != nil {
                NavigationLink(destination: PanoramaView(panoramaImage: panoramaImage!)) {
                    Text("View Panorama")
                        .font(.headline)
                        .foregroundColor(.white)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 14)
                        .background(Color.blue, in: RoundedRectangle(cornerRadius: 12))
                }
                .padding(.horizontal)
                .padding(.bottom, 24)
            }
        }
        .navigationTitle("Processing")
        .navigationBarTitleDisplayMode(.inline)
        .navigationBarBackButtonHidden(phase == .uploading || phase == .stitching)
        .task { await startProcessing() }
    }

    private func startProcessing() async {
        do {
            // Phase 1: Upload
            phase = .uploading
            uploadProgress = 0

            try await APIService.shared.uploadImages(
                projectId: projectId,
                images: images,
                onProgress: { progress in
                    Task { @MainActor in uploadProgress = progress }
                }
            )

            // Phase 2: Poll for stitching completion
            phase = .stitching

            while true {
                try await Task.sleep(nanoseconds: 2_000_000_000) // 2 seconds
                let status = try await APIService.shared.getProjectStatus(projectId: projectId)

                if status.status == "stitched" || status.status == "staged" || status.status == "complete" {
                    // Download the panorama
                    panoramaImage = try await APIService.shared.getPanorama(projectId: projectId)
                    phase = .done
                    HapticManager.success()
                    break
                } else if status.status == "failed" {
                    error = status.error ?? "Unknown error"
                    phase = .failed
                    HapticManager.error()
                    break
                }
            }
        } catch {
            self.error = error.localizedDescription
            phase = .failed
            HapticManager.error()
        }
    }
}
