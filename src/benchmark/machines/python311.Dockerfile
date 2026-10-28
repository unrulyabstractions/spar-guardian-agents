# CheatBench's sandbox image for its python311 tasks (submodules/cheatbench/Dockerfile: an interpreter and the
# libraries a row's work needs, nothing that identifies the eval), plus what its runner does at episode start: the
# unprivileged agent user owning /workspace.
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates git \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir \
        numpy \
        sympy \
        mpmath \
        matplotlib \
        pillow==12.3.0 \
        requests==2.34.2 \
        pyyaml==6.0.3
RUN mkdir -p /workspace
WORKDIR /workspace
RUN useradd --uid 1000 --create-home --home-dir /home/user --shell /bin/bash user \
    && chown user:user /workspace
