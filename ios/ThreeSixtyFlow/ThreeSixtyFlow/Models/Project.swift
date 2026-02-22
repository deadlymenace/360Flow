import Foundation

struct Project: Codable, Identifiable {
    let id: String
    var name: String
    var status: String
    var createdAt: Date
    var imageCount: Int
    var panoramaAvailable: Bool

    enum CodingKeys: String, CodingKey {
        case id
        case name
        case status
        case createdAt = "created_at"
        case imageCount = "image_count"
        case panoramaPath = "panorama_path"
    }

    init(id: String, name: String, status: String, createdAt: Date, imageCount: Int, panoramaAvailable: Bool) {
        self.id = id
        self.name = name
        self.status = status
        self.createdAt = createdAt
        self.imageCount = imageCount
        self.panoramaAvailable = panoramaAvailable
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(String.self, forKey: .id)
        name = try container.decode(String.self, forKey: .name)
        status = try container.decode(String.self, forKey: .status)
        createdAt = try container.decode(Date.self, forKey: .createdAt)
        imageCount = try container.decode(Int.self, forKey: .imageCount)
        let panoramaPath = try container.decodeIfPresent(String.self, forKey: .panoramaPath)
        panoramaAvailable = panoramaPath != nil
    }
}
