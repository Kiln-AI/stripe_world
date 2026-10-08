# Required by `openenv push`: a Space is a Docker container, and a root
# Dockerfile is what lets one be pushed without a server/ directory.
FROM ghcr.io/huggingface/openenv-base:latest

# openenv-base:latest is python:3.11-slim with uv 0.5.27, which can download no
# final CPython 3.14, so the `uv sync` below fails with "No interpreter found for
# Python >=3.14". This is the uv that wrote uv.lock; it downloads 3.14 itself.
COPY --from=ghcr.io/astral-sh/uv:0.12.18 /uv /uvx /usr/local/bin/

WORKDIR /app
COPY . /app

# A Hugging Face Space runs the container as uid 1000, and the build runs as root.
# uv's default Python directory is under /root, which is mode 700, so the venv's
# interpreter would not be readable at run time.
ENV UV_PYTHON_INSTALL_DIR=/opt/uv/python

# The serve extra: this image runs the OpenEnv server, which the in-process world
# does not need.
RUN uv sync --extra serve

# OpenEnv's container providers start the server as `python -m uvicorn <app>`,
# with `app` from openenv.yaml, so `python` has to be this world's interpreter.
ENV PATH="/app/.venv/bin:$PATH"

# uid 1000 cannot write a uv cache under $HOME or change the root-owned venv,
# so `uv run` in the CMD must neither cache nor sync.
ENV UV_NO_CACHE=1 UV_NO_SYNC=1

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 CMD \
    python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"
CMD ["uv", "run", "uvicorn", "seahaven_stripe_world.openenv_app:app", "--host", "0.0.0.0", "--port", "8000"]
