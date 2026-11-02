FROM --platform=linux/amd64 public.ecr.aws/docker/library/python:3.11.17-slim-trixie@sha256:e529028263dbe6910a2d96f7d2b8f5266385e917fd45d286ef166977c094a51e

WORKDIR /app
COPY backend/scripts/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/scripts/setup_opensearch_index.py ./setup_opensearch_index.py
COPY data ./data

RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser
# No HEALTHCHECK (checkov CKV_DOCKER_2): this image runs as a one-off ECS task that
# exits when loading finishes. Success is the container exit code, not a probe.
CMD ["python", "setup_opensearch_index.py", "--data-dir", "/app/data"]
