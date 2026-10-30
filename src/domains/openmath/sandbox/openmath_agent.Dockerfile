# CheatBench's python311 sandbox image (src/sandbox/machines/python311.Dockerfile, built as cbtask:python311: the
# interpreter, the libraries and the unprivileged agent user owning /workspace), plus what openmath's runner adds:
# the reading CA, and iptables for the loopback rules between agent users (src/sandbox/sandbox_hygiene.py).
FROM cbtask:python311
RUN apt-get update && apt-get install -y --no-install-recommends iptables \
    && rm -rf /var/lib/apt/lists/*
COPY ca.crt /usr/local/share/ca-certificates/internal-reading-ca.crt
RUN update-ca-certificates \
    && cat /usr/local/share/ca-certificates/internal-reading-ca.crt >> "$(python -c 'import certifi; print(certifi.where())')"
