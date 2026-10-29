# CheatBench's sandbox image (submodules/cheatbench/Dockerfile), plus what its
# runner does at episode start: the unprivileged agent user, and the reading CA.
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates git iptables \
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
COPY ca.crt /usr/local/share/ca-certificates/internal-reading-ca.crt
RUN update-ca-certificates \
    && cat /usr/local/share/ca-certificates/internal-reading-ca.crt >> "$(python -c 'import certifi; print(certifi.where())')"
