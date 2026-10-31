FROM python:3.12-slim

WORKDIR /app

# Install deps first so this layer is cached across code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the source. Secrets (.env) and logs are NOT baked in — see .dockerignore;
# .env is passed at runtime and logs are a mounted volume.
COPY *.py .

# Unbuffered output so logs show up immediately in `docker logs`.
ENV PYTHONUNBUFFERED=1

CMD ["python", "main.py"]
