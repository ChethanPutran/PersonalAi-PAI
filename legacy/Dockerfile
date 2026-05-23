FROM python:3.10-slim

WORKDIR /app

# Install system dependencies for audio (if needed)
RUN apt-get update && apt-get install -y \
    gcc \
    portaudio19-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements-backend.txt .
RUN pip install --no-cache-dir -r requirements-backend.txt

# Copy your existing code
COPY src/ ./src/
COPY skills/ ./skills/
COPY backend/ ./backend/
COPY run_backend.py .

# Expose port
EXPOSE 8000

# Run backend server
CMD ["python", "run_backend.py", "--host", "0.0.0.0", "--port", "8000"]