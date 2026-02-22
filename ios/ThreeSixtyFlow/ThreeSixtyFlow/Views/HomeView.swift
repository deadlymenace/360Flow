import SwiftUI

struct HomeView: View {
    @StateObject private var viewModel = ProjectViewModel()

    @State private var showNewCapture = false
    @State private var showNamePrompt = false
    @State private var newProjectName = ""
    @State private var activeProject: Project?

    // Capture flow state
    @State private var navigateToReview = false
    @State private var navigateToProcessing = false
    @State private var captureVM: CaptureViewModel?

    var body: some View {
        NavigationStack {
            Group {
                if viewModel.projects.isEmpty && !viewModel.isLoading {
                    emptyState
                } else {
                    projectList
                }
            }
            .navigationTitle("360Flow")
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        newProjectName = ""
                        showNamePrompt = true
                    } label: {
                        Image(systemName: "plus")
                    }
                }
            }
            .alert("New Capture", isPresented: $showNamePrompt) {
                TextField("Room name", text: $newProjectName)
                Button("Cancel", role: .cancel) {}
                Button("Start") {
                    Task {
                        if let project = await viewModel.createProject(name: newProjectName.isEmpty ? "Untitled Room" : newProjectName) {
                            activeProject = project
                            showNewCapture = true
                        }
                    }
                }
            } message: {
                Text("Give this room a name")
            }
            .navigationDestination(isPresented: $showNewCapture) {
                CaptureView(
                    navigateToReview: $navigateToReview,
                    captureVM: $captureVM
                )
            }
            .navigationDestination(isPresented: $navigateToReview) {
                if let captureVM {
                    CaptureReviewView(
                        viewModel: captureVM,
                        navigateToProcessing: $navigateToProcessing
                    )
                }
            }
            .navigationDestination(isPresented: $navigateToProcessing) {
                if let project = activeProject, let captureVM {
                    ProcessingView(
                        projectId: project.id,
                        images: captureVM.orderedImages()
                    )
                }
            }
            .task { await viewModel.loadProjects() }
            .refreshable { await viewModel.loadProjects() }
        }
    }

    private var emptyState: some View {
        VStack(spacing: 16) {
            Image(systemName: "camera.viewfinder")
                .font(.system(size: 64))
                .foregroundColor(.secondary)

            Text("No captures yet")
                .font(.title3)
                .foregroundColor(.secondary)

            Button {
                newProjectName = ""
                showNamePrompt = true
            } label: {
                Label("New Capture", systemImage: "plus")
                    .font(.headline)
                    .padding(.horizontal, 24)
                    .padding(.vertical, 12)
                    .background(Color.blue, in: Capsule())
                    .foregroundColor(.white)
            }
        }
    }

    private var projectList: some View {
        List(viewModel.projects) { project in
            ProjectRow(project: project)
        }
    }
}

struct ProjectRow: View {
    let project: Project

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 4) {
                Text(project.name)
                    .font(.headline)
                Text(project.statusLabel)
                    .font(.caption)
                    .foregroundColor(statusColor)
            }

            Spacer()

            Text("\(project.imageCount) photos")
                .font(.caption)
                .foregroundColor(.secondary)
        }
        .padding(.vertical, 4)
    }

    var statusColor: Color {
        switch project.status {
        case "stitched", "staged", "complete": return .green
        case "failed": return .red
        default: return .orange
        }
    }
}
