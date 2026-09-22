# Run backend
source .env && PYTHONPATH=src uvicorn pai.main:app --reload --host 0.0.0.0 --port 8000

# Run frontend
cd frontend/ui/apps/pai_mobile && flutter pub get && flutter run -d <device-id>
