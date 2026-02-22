import SceneKit
import SwiftUI

struct PanoramaView: View {
    let panoramaImage: UIImage

    var body: some View {
        SceneView(scene: makePanoramaScene(), options: [.allowsCameraControl])
            .ignoresSafeArea()
    }

    private func makePanoramaScene() -> SCNScene {
        let scene = SCNScene()
        let sphere = SCNSphere(radius: 10)
        sphere.segmentCount = 96
        let material = SCNMaterial()
        material.diffuse.contents = panoramaImage
        material.isDoubleSided = true
        sphere.firstMaterial = material

        let sphereNode = SCNNode(geometry: sphere)
        sphereNode.scale = SCNVector3(x: -1, y: 1, z: 1)
        scene.rootNode.addChildNode(sphereNode)

        let cameraNode = SCNNode()
        cameraNode.camera = SCNCamera()
        cameraNode.camera?.fieldOfView = 70
        cameraNode.position = SCNVector3(x: 0, y: 0, z: 0)
        scene.rootNode.addChildNode(cameraNode)

        return scene
    }
}
