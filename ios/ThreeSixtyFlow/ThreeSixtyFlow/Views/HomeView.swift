import SwiftUI

struct HomeView: View {
    @ObservedObject var projectViewModel: ProjectViewModel
    let onNewCapture: () -> Void

    var body: some View {
        VStack {
            if projectViewModel.projects.isEmpty {
                ContentUnavailableView("No captures yet", systemImage: "camera.metering.center.weighted", description: Text("Tap + to start."))
            } else {
                List(projectViewModel.projects) { project in
                    HStack {
                        VStack(alignment: .leading) {
                            Text(project.name).font(.headline)
                            Text(project.status).font(.caption)
                        }
                        Spacer()
                        if project.panoramaAvailable {
                            Image(systemName: "photo")
                        }
                    }
                }
            }
        }
        .navigationTitle("360Flow")
        .toolbar {
            Button(action: onNewCapture) {
                Image(systemName: "plus.circle.fill")
            }
        }
        .task {
            await projectViewModel.refreshProjects()
        }
    }
}
