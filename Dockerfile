# Base image
FROM python:3.11-slim

# Set work directory
WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project
COPY . .

# Expose the default FastAPI port
EXPOSE 8080

# Command to run the app
CMD ["fastapi", "run"]
