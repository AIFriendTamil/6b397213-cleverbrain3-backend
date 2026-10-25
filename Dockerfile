# ==========================================
# Stage 1: Build dependencies in virtualenv
# ==========================================
FROM python:3.14.8-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install system build dependencies if required
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency specifications first for optimal layer caching
COPY pyproject.toml requirements.txt ./

# Create virtual environment and install Python dependencies
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir -r requirements.txt

# ==========================================
# Stage 2: Production runtime image
# ==========================================
FROM python:3.14.8-slim AS runner

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app

WORKDIR /app

# Create non-root system group and user
RUN groupadd -g 10001 appgroup && \
    useradd -u 10001 -g appgroup -s /bin/sh -m appuser

# Copy installed dependencies from builder stage
COPY --from=builder /opt/venv /opt/venv

# Copy application source code and specifications
COPY . /app/

# Set ownership of application directory to non-root user
RUN chown -R appuser:appgroup /app

# Switch to non-root user
USER appuser

# Expose FastAPI HTTP server default port
EXPOSE 8000

# Default command starts FastAPI ASGI server (can be overridden by worker service)
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
