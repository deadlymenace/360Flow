import Foundation
import UIKit

struct ProjectStatusResponse: Codable {
    let status: String
    let progress: Int
    let error: String?
}

class APIService {
    static let shared = APIService()
    private let baseURL: String

    init(baseURL: String = "http://localhost:8000/api/v1") {
        self.baseURL = baseURL
    }

    func createProject(name: String) async throws -> Project {
        let url = URL(string: "\(baseURL)/projects")!
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: ["name": name])

        let (data, _) = try await URLSession.shared.data(for: request)
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return try decoder.decode(Project.self, from: data)
    }

    func listProjects() async throws -> [Project] {
        let url = URL(string: "\(baseURL)/projects")!
        let (data, _) = try await URLSession.shared.data(from: url)
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return try decoder.decode([Project].self, from: data)
    }

    func uploadImages(projectId: String, images: [UIImage], onProgress: @escaping (Double) -> Void) async throws {
        let boundary = "Boundary-\(UUID().uuidString)"
        let url = URL(string: "\(baseURL)/projects/\(projectId)/upload")!
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")

        var body = Data()
        let total = max(images.count, 1)

        for (idx, image) in images.enumerated() {
            guard let data = image.jpegData(compressionQuality: 0.8) else { continue }
            body.append("--\(boundary)\r\n".data(using: .utf8)!)
            body.append("Content-Disposition: form-data; name=\"images\"; filename=\"image_\(idx).jpg\"\r\n".data(using: .utf8)!)
            body.append("Content-Type: image/jpeg\r\n\r\n".data(using: .utf8)!)
            body.append(data)
            body.append("\r\n".data(using: .utf8)!)
            onProgress(Double(idx + 1) / Double(total))
        }
        body.append("--\(boundary)--\r\n".data(using: .utf8)!)

        let (_, response) = try await URLSession.shared.upload(for: request, from: body)
        guard let http = response as? HTTPURLResponse, 200..<300 ~= http.statusCode else {
            throw URLError(.badServerResponse)
        }
    }

    func getProjectStatus(projectId: String) async throws -> ProjectStatusResponse {
        let url = URL(string: "\(baseURL)/projects/\(projectId)/status")!
        let (data, _) = try await URLSession.shared.data(from: url)
        return try JSONDecoder().decode(ProjectStatusResponse.self, from: data)
    }

    func getPanorama(projectId: String) async throws -> UIImage {
        let url = URL(string: "\(baseURL)/projects/\(projectId)/panorama")!
        let (data, _) = try await URLSession.shared.data(from: url)
        guard let image = UIImage(data: data) else {
            throw URLError(.cannotDecodeRawData)
        }
        return image
    }
}
