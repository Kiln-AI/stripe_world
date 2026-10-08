# Required by `openenv push`: a Space is a Docker container, and a root
# Dockerfile is what lets one be pushed without a server/ directory.
FROM ghcr.io/huggingface/openenv-base:latest

# The base image's uv is 0.5.27, which can download no final CPython 3.14, so
# `uv sync` would stop with "No interpreter found for Python >=3.14". uv releases
# often and a release can change what it resolves or the CPython it installs, so
# this is an exact version: a rebuild of the Space gets the same uv.
COPY --from=ghcr.io/astral-sh/uv:0.12.18 /uv /uvx /usr/local/bin/

# pyproject.toml pins Seahaven to a git commit, which uv fetches with the git
# executable, and openenv-base ships none.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app

# A Hugging Face Space runs the container as uid 1000, and the build runs as root.
# uv's default directory for the CPython it downloads is under /root, which is mode
# 700, so the venv's interpreter would not be readable at run time.
ENV UV_PYTHON_INSTALL_DIR=/opt/uv/python

# The serve extra: this image runs the OpenEnv server, which the in-process world
# does not need.
RUN uv sync --extra serve

# OpenEnv's container providers start the server as `python -m uvicorn <app>`,
# with `app` from openenv.yaml, so `python` has to be this world's interpreter.
# The CMD starts it the same way, because `uv run` as uid 1000 can neither write a
# cache nor sync the root-owned venv.
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000
# The venv's interpreter by path: a bare `python` falls through to the base image's
# when the venv's is unreadable, and the check would pass on an image that cannot serve.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 CMD \
    /app/.venv/bin/python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"
CMD ["python", "-m", "uvicorn", "seahaven_stripe_world.openenv_app:app", "--host", "0.0.0.0", "--port", "8000"]
