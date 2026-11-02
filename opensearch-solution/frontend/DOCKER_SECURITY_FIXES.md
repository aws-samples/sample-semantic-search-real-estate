# Non-production container security notes

This sample builds from the Python 3.11 official image mirrored in Amazon ECR Public (`public.ecr.aws/docker/library/python`), pinned by digest, and runs Streamlit as UID 10001 rather than as root. Direct Python dependencies are pinned, package installation uses `--no-cache-dir`, and the build context excludes local environments, credentials, caches, Compose files, and repository metadata.

The tracked Streamlit configuration enables CORS and XSRF protections. The container listens on all container interfaces because the ECS task and internal load balancer communicate over the task network. Docker Compose publishes that port only on `127.0.0.1`. The production AWS network shape is still outside the container: CloudFront VPC origin, internal ALB, private ECS task, and security groups.

The image health check uses Python's standard library, so no extra operating-system package is installed. Runtime configuration contains service identifiers and endpoints only. The demo password is neither injected into nor read by the frontend container.

Local checks:

```bash
docker build --check -f Dockerfile .
docker build -t semantic-search-local-check .
docker run --rm --entrypoint id semantic-search-local-check
```

A non-zero UID confirms the configured user. These checks do not establish production readiness or compliance. Scan rebuilt images for current vulnerabilities and apply your organization's image provenance, patching, signing, runtime, and deployment controls before any production use.
