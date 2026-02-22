import SwiftUI
import SceneKit

struct PanoramaView: View {
    let panoramaImage: UIImage

    var body: some View {
        SceneView(
            scene: makePanoramaScene(),
            pointOfView: nil,
            options: [.allowsCameraControl]
        )
        .ignoresSafeArea()
        .navigationTitle("360° Panorama")
        .navigationBarTitleDisplayMode(.inline)
    }

    private func makePanoramaScene() -> SCNScene {
        let scene = SCNScene()

        // Create a large sphere
        let sphere = SCNSphere(radius: 10)
        sphere.segmentCount = 96

        // Apply the equirectangular panorama as a texture on the inside
        let material = SCNMaterial()
        material.diffuse.contents = panoramaImage
        material.isDoubleSided = true
        material.diffuse.wrapS = .repeat
        material.diffuse.wrapT = .repeat
        sphere.firstMaterial = material

        let sphereNode = SCNNode(geometry: sphere)
        // Flip normals so the texture renders on the inside of the sphere
        sphereNode.scale = SCNVector3(x: -1, y: 1, z: 1)
        scene.rootNode.addChildNode(sphereNode)

        // Place camera at the center of the sphere
        let cameraNode = SCNNode()
        cameraNode.camera = SCNCamera()
        cameraNode.camera?.fieldOfView = 70
        cameraNode.camera?.zNear = 0.1
        cameraNode.camera?.zFar = 100
        cameraNode.position = SCNVector3(x: 0, y: 0, z: 0)
        scene.rootNode.addChildNode(cameraNode)

        return scene
    }
}
