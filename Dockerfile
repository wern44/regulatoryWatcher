FROM python:3.12-slim AS base

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies needed by pdfplumber/lxml
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        libxml2-dev \
        libxslt1-dev && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (cached layer)
COPY pyproject.toml ./
RUN pip install --no-cache-dir .

# Copy application code and seeds
COPY regwatch/ regwatch/
COPY seeds/ seeds/
COPY config.example.yaml ./
COPY docker-entrypoint.sh ./

RUN chmod +x docker-entrypoint.sh

# Data directory — will be mounted as a volume
RUN mkdir -p /app/data/pdfs /app/data/uploads

EXPOSE 8001

ENTRYPOINT ["./docker-entrypoint.sh"]
