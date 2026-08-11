import 'package:camera/camera.dart';
import 'package:image_picker/image_picker.dart';

class CameraService {
  static Future<XFile?> captureImage() async {
    final cameras = await availableCameras();
    final camera = cameras.first;
    final controller = CameraController(camera, ResolutionPreset.medium);
    await controller.initialize();
    final XFile image = await controller.takePicture();
    await controller.dispose();
    return image;
  }

  static Future<XFile?> pickImageFromGallery() async {
    final picker = ImagePicker();
    return await picker.pickImage(source: ImageSource.gallery);
  }
}