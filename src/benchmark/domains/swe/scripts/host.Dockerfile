# The amd64 image on an amd64 host (see benchmark/domains/swe/sandbox.py): what native.Dockerfile adds, on top of it.
FROM {source}
RUN command -v python3 >/dev/null || (apt-get update && apt-get install -y --no-install-recommends python3 && rm -rf /var/lib/apt/lists/*)
# bash --login (Inspect's bash tool) resets PATH in /etc/profile; restore the image's PATH.
RUN printf 'export PATH=%s\n' {path_q} > /etc/profile.d/00-image-path.sh
RUN mkdir -p /logs
LABEL cbswe.source_image_id={source_id} cbswe.recipe_sha256={recipe}
