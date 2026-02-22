import SwiftUI

struct ContentView: View {
    @StateObject private var projectViewModel = ProjectViewModel()
    @StateObject private var captureViewModel = CaptureViewModel()

    @State private var path = NavigationPath()

    var body: some View {
        NavigationStack(path: $path) {
            HomeView(projectViewModel: projectViewModel) {
                Task {
                    await projectViewModel.createProject(name: "New Room")
                    path.append(Route.capture)
                }
            }
            .navigationDestination(for: Route.self) { route in
                switch route {
                case .capture:
                    CaptureView(viewModel: captureViewModel) {
                        path.append(Route.review)
                    }
                case .review:
                    CaptureReviewView(captureViewModel: captureViewModel) {
                        path.append(Route.processing)
                    }
                case .processing:
                    if let id = projectViewModel.activeProject?.id {
                        ProcessingView(projectId: id,
                                       images: captureViewModel.segments.compactMap(\ .image)) { panorama in
                            projectViewModel.panoramaImage = panorama
                            path.append(Route.panorama)
                        }
                    }
                case .panorama:
                    if let image = projectViewModel.panoramaImage {
                        PanoramaView(panoramaImage: image)
                    } else {
                        Text("No panorama yet")
                    }
                }
            }
        }
    }
}

enum Route: Hashable {
    case capture
    case review
    case processing
    case panorama
}
