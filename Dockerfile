FROM python:3.11-slim

# Install ffmpeg with root access during the image build.
# (This is the step that failed under Render's native Python buildpack —
# that environment doesn't grant apt-get root access during build.)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render sets $PORT at runtime; default to 8000 for local testing.
ENV PORT=8000
CMD uvicorn app:app --host 0.0.0.0 --port ${PORT}
