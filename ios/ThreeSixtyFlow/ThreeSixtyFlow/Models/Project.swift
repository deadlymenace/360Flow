import Foundation

struct Project: Codable, Identifiable {
    let id: String
    var name: String
    var status: String
    var createdAt: Date
    var imageCount: Int
    var panoramaPath: String?
    var error: String?

    enum CodingKeys: String, CodingKey {
        case id, name, status
        case createdAt = "created_at"
        case imageCount = "image_count"
        case panoramaPath = "panorama_path"
        case error
    }

    var isStitched: Bool {
        status == "stitched" || status == "staged" || status == "complete"
    }

    var statusLabel: String {
        switch status {
        case "created": return "New"
        case "uploading": return "Uploading..."
        case "stitching": return "Stitching..."
        case "stitched": return "Ready"
        case "staging": return "Staging..."
        case "staged": return "Staged"
        case "generating_3d": return "Generating 3D..."
        case "complete": return "Complete"
        case "failed": return "Failed"
        default: return status.capitalized
        }
    }
}
