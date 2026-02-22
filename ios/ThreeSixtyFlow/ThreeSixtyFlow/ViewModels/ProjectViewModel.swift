import SwiftUI

@MainActor
class ProjectViewModel: ObservableObject {
    @Published var projects: [Project] = []
    @Published var isLoading = false
    @Published var error: String?

    func loadProjects() async {
        isLoading = true
        error = nil
        do {
            projects = try await APIService.shared.listProjects()
        } catch {
            self.error = error.localizedDescription
        }
        isLoading = false
    }

    func createProject(name: String) async -> Project? {
        do {
            let project = try await APIService.shared.createProject(name: name)
            projects.insert(project, at: 0)
            return project
        } catch {
            self.error = error.localizedDescription
            return nil
        }
    }
}
