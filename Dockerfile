FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Dependencies first so the layer caches across source edits.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# Smithery sets PORT and proxies to /mcp on it.
ENV PORT=8080
EXPOSE 8080

# Run as a non-root user. The server needs no write access to anything.
RUN useradd --create-home --uid 10001 trellis
USER trellis

CMD ["python", "-m", "trellis.smithery_app"]
