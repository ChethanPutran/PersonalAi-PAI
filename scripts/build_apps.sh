BUILD_DIR="/home/chethan/Desktop/Chethan/aiml_projects/advanced_projects/personal_ai/ai_personal_agent/data/builds"

rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR/android" "$BUILD_DIR/linux"

flutter build apk --release
cp build/app/outputs/flutter-apk/app-release.apk "$BUILD_DIR/android/"

flutter build linux --release
cp -r build/linux/x64/release/bundle/* "$BUILD_DIR/linux/"