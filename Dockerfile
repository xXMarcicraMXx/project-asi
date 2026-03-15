FROM python:3.12-slim

# Prevents .pyc files and enables real-time log output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install curl (healthcheck) and pip deps
COPY requirements.txt .
RUN apt-get update && apt-get install -y --no-install-recommends curl rsync openssh-client \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Non-root user for security
RUN useradd -m -u 1000 asi && chown -R asi:asi /app
USER asi

# Webhook server + scheduler (scheduler implemented Day 19)
CMD ["python", "app.py"]
