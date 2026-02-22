import Foundation
import UIKit

@MainActor
class ProjectViewModel: ObservableObject {
    @Published var projects: [Project] = []
    @Published var activeProject: Project?
    @Published var panoramaImage: UIImage?
    @Published var processingError: String?

    func refreshProjects() async {
        do {
            projects = try await APIService.shared.listProjects()
        } catch {
            processingError = error.localizedDescription
        }
    }

    func createProject(name: String) async {
        do {
            activeProject = try await APIService.shared.createProject(name: name)
            await refreshProjects()
        } catch {
            processingError = error.localizedDescription
        }
    }
}
