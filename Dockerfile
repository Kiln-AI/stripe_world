# Required by `openenv push`: a Space is a Docker container, and a root
# Dockerfile is what lets one be pushed without a server/ directory.
FROM ghcr.io/huggingface/openenv-base:latest

# openenv-base:latest is python:3.11-slim with uv 0.5.27, which can download no
# final CPython 3.14, so the `uv sync` below fails with "No interpreter found for
# Python >=3.14". This is the uv that wrote uv.lock; it downloads 3.14 itself.
COPY --from=ghcr.io/astral-sh/uv:0.12.18 /uv /uvx /usr/local/bin/

WORKDIR /app
COPY . /app

# The serve extra: this image runs the OpenEnv server, which the in-process world
# does not need.
RUN uv sync --extra serve

# OpenEnv's container providers start the server as `python -m uvicorn <app>`,
# with `app` from openenv.yaml, so `python` has to be this world's interpreter.
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 CMD \
    python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"
CMD ["uv", "run", "uvicorn", "seahaven_stripe_world.openenv_app:app", "--host", "0.0.0.0", "--port", "8000"]
