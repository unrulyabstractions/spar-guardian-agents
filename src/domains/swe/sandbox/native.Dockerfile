# Native arm64 rebuild of {source} (see benchmark/swe.py).
FROM --platform=linux/amd64 {source} AS src
FROM golang:{go_version}-{codename}
RUN apt-get update && apt-get install -y --no-install-recommends python3 && rm -rf /var/lib/apt/lists/*
COPY --from=src /go/pkg/mod /go/pkg/mod
COPY --from=src /app /app
WORKDIR /app
{env}
# bash --login (Inspect's bash tool) resets PATH in /etc/profile; restore the image's PATH.
RUN printf 'export PATH=%s\n' {path_q} > /etc/profile.d/00-image-path.sh
RUN mkdir -p /logs
LABEL cbswe.source_image_id={source_id} cbswe.recipe_sha256={recipe}
