import SwiftUI

struct ProcessingView: View {
    let projectId: String
    let images: [UIImage]
    let onComplete: (UIImage) -> Void

    @State private var uploadProgress: Double = 0
    @State private var isUploading = true
    @State private var statusText = "Uploading"
    @State private var errorMessage: String?

    var body: some View {
        VStack(spacing: 16) {
            if let errorMessage {
                Text(errorMessage).foregroundStyle(.red)
                Button("Retry") { Task { await process() } }
            } else if isUploading {
                Text("Uploading")
                ProgressView(value: uploadProgress)
            } else {
                Text(statusText)
                ProgressView()
            }
        }
        .task { await process() }
        .padding()
    }

    private func process() async {
        do {
            isUploading = true
            try await APIService.shared.uploadImages(projectId: projectId, images: images) { progress in
                Task { @MainActor in uploadProgress = progress }
            }
            isUploading = false
            statusText = "Stitching your panorama..."

            while true {
                let status = try await APIService.shared.getProjectStatus(projectId: projectId)
                if status.status == "stitched" {
                    let panorama = try await APIService.shared.getPanorama(projectId: projectId)
                    HapticManager.success()
                    onComplete(panorama)
                    break
                } else if status.status == "failed" {
                    throw NSError(domain: "Processing", code: 1, userInfo: [NSLocalizedDescriptionKey: status.error ?? "Stitching failed"])
                }
                try await Task.sleep(nanoseconds: 2_000_000_000)
            }
        } catch {
            HapticManager.error()
            errorMessage = error.localizedDescription
        }
    }
}
