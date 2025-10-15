# ---- Base Stage ----
FROM python:3.12.5-slim as base

# Set environment variables to make Python and pip run in a container-friendly way
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=off \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_DEFAULT_TIMEOUT=100

# Install uv, the package manager
RUN pip install uv

# ---- Builder Stage ----
FROM base as builder

WORKDIR /app

# Copy only the necessary dependency files
COPY requirements.txt uv.lock ./

# Install dependencies using uv for speed and to leverage the lock file
# Using --system to install packages into the system site-packages, which is standard for containers
RUN uv pip sync --system --no-cache --frozen-lockfile uv.lock

# ---- Final Stage ----
FROM base as final

# Create a dedicated, non-root user for running the application to enhance security
RUN addgroup --system nonroot && adduser --system --ingroup nonroot nonroot
USER nonroot

WORKDIR /app

# Copy the installed dependencies from the builder stage
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin/uvicorn /usr/local/bin/uvicorn

# Copy the application code, ensuring the non-root user has ownership
COPY --chown=nonroot:nonroot app/ ./app/

# Set the port for the application to run on
ENV PORT=8000
EXPOSE 8000

# The command to run the application using uvicorn
# The port is explicitly set here to match the EXPOSE instruction
CMD ["uvicorn", "app.main:app", "--host=0.0.0.0", "--port", "8000"]
