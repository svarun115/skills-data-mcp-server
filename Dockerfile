FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --upgrade pip hatchling && pip install --no-cache-dir .

# Create data directory
RUN mkdir -p /data/resources

RUN useradd -m -u 1000 skillsuser \
    && chown -R skillsuser:skillsuser /app /data/resources
USER skillsuser

EXPOSE 6666

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -sf http://localhost:6666/healthz || exit 1

CMD ["python", "-m", "skills_data_mcp", "--http", "--host", "0.0.0.0", "--port", "6666"]
