# ---- Base Stage ----
FROM python:3.12.5-slim as base

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=off \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_DEFAULT_TIMEOUT=100

# Install uv
RUN pip install uv

# ---- Builder Stage ----
FROM base as builder

WORKDIR /app

# Copy dependency files
COPY requirements.txt pyproject.toml ./

# Install dependencies using uv
RUN uv pip install -r requirements.txt --no-cache --system

# ---- Final Stage ----
FROM base as final

# Install only runtime dependencies (not build tools)
WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Create non-root user
RUN addgroup --system nonroot && adduser --system --ingroup nonroot nonroot

# Copy application code with proper ownership
COPY --chown=nonroot:nonroot app/ ./app/

# Switch to non-root user
USER nonroot

# Cloud Run will set PORT dynamically, default to 8080
ENV PORT=8080
EXPOSE 8080

# Health check (optional but recommended)
HEALTHCHECK CMD curl --fail http://localhost:${PORT}/ || exit 1

# Run uvicorn with dynamic port
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1