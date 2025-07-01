# syntax=docker/dockerfile:1
FROM python:3.11-alpine

# Install system dependencies and build tools for Alpine
RUN apk update && \
    apk add --no-cache --virtual .build-deps build-base gcc musl-dev && \
    apk add --no-cache postgresql-dev libffi-dev && \
    pip install --no-cache-dir --upgrade pip

# Set work directory
WORKDIR /app

# Install pipenv or poetry if you use them (uncomment if needed)
# RUN pip install pipenv
# COPY Pipfile* ./
# RUN pipenv install --deploy --ignore-pipfile

# Install Python dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Remove build dependencies to reduce image size
RUN apk del .build-deps

# Copy app code
COPY ./app ./app
COPY ./alembic.ini ./
COPY ./migration ./migration

# Copy entrypoint script (for migrations, optional)
# COPY ./docker-entrypoint.sh ./
# RUN chmod +x /app/docker-entrypoint.sh

# Expose port
EXPOSE 8000

# Set environment variables (can be overridden by docker-compose)
ENV PYTHONUNBUFFERED=1

# Start the app with uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"] 