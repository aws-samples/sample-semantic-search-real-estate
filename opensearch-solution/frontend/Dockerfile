FROM --platform=linux/amd64 public.ecr.aws/docker/library/python:3.11.17-slim-trixie@sha256:e529028263dbe6910a2d96f7d2b8f5266385e917fd45d286ef166977c094a51e

RUN groupadd --system appuser && useradd --system --gid appuser --uid 10001 --home-dir /app appuser
WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN chown -R appuser:appuser /app && chmod +x entrypoint.sh

EXPOSE 8501
USER appuser
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health', timeout=4)"]
CMD ["./entrypoint.sh"]
