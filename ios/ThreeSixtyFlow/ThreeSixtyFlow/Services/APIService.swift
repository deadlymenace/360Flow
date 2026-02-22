import UIKit

class APIService {
    static let shared = APIService()

    // Change this to your Mac's local IP when testing on a physical device
    private let baseURL: String

    init(baseURL: String = "http://localhost:8000/api/v1") {
        self.baseURL = baseURL
    }

    // MARK: - Create Project

    func createProject(name: String) async throws -> Project {
        let url = URL(string: "\(baseURL)/projects")!
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(["name": name])

        let (data, _) = try await URLSession.shared.data(for: request)
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return try decoder.decode(Project.self, from: data)
    }

    // MARK: - List Projects

    func listProjects() async throws -> [Project] {
        let url = URL(string: "\(baseURL)/projects")!
        let (data, _) = try await URLSession.shared.data(from: url)
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return try decoder.decode([Project].self, from: data)
    }

    // MARK: - Upload Images

    func uploadImages(
        projectId: String,
        images: [UIImage],
        onProgress: @escaping (Double) -> Void
    ) async throws {
        let url = URL(string: "\(baseURL)/projects/\(projectId)/upload")!
        let boundary = UUID().uuidString

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")

        var body = Data()
        for (i, image) in images.enumerated() {
            guard let jpegData = image.jpegData(compressionQuality: 0.8) else { continue }
            let filename = String(format: "segment_%02d.jpg", i)

            body.append("--\(boundary)\r\n".data(using: .utf8)!)
            body.append("Content-Disposition: form-data; name=\"images\"; filename=\"\(filename)\"\r\n".data(using: .utf8)!)
            body.append("Content-Type: image/jpeg\r\n\r\n".data(using: .utf8)!)
            body.append(jpegData)
            body.append("\r\n".data(using: .utf8)!)

            onProgress(Double(i + 1) / Double(images.count))
        }
        body.append("--\(boundary)--\r\n".data(using: .utf8)!)

        request.httpBody = body
        let _ = try await URLSession.shared.data(for: request)
    }

    // MARK: - Poll Status

    struct StatusResponse: Codable {
        let status: String
        let progress: Int
        let error: String?
    }

    func getProjectStatus(projectId: String) async throws -> StatusResponse {
        let url = URL(string: "\(baseURL)/projects/\(projectId)/status")!
        let (data, _) = try await URLSession.shared.data(from: url)
        return try JSONDecoder().decode(StatusResponse.self, from: data)
    }

    // MARK: - Get Panorama

    func getPanorama(projectId: String) async throws -> UIImage {
        let url = URL(string: "\(baseURL)/projects/\(projectId)/panorama")!
        let (data, _) = try await URLSession.shared.data(from: url)
        guard let image = UIImage(data: data) else {
            throw URLError(.cannotDecodeContentData)
        }
        return image
    }
}
