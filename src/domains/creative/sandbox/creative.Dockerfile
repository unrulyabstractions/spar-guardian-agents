# Creative writing's two images, both on CheatBench's python311 image (src/sandbox/machines/python311.Dockerfile, built
# as cbtask:python311): the agent's (cbcw:agent) and the review sidecar's (cbcw:review). CheatBench's score_ext.c is
# compiled once against that image's interpreter, and both images carry the result: the grader in the workspace finds
# a copy beside itself, and the sidecar compares with it. iptables serves the loopback rules between agent users
# (src/sandbox/sandbox_hygiene.py); iproute2 gives the sidecar its loopback alias.
FROM cbtask:python311 AS score
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev && rm -rf /var/lib/apt/lists/*
COPY score_ext.c /build/score_ext.c
RUN gcc -shared -fPIC -O2 -I"$(python3 -c 'import sysconfig; print(sysconfig.get_path("include"))')" \
        /build/score_ext.c -o /build/_score.so

FROM cbtask:python311 AS agent
RUN apt-get update && apt-get install -y --no-install-recommends iptables && rm -rf /var/lib/apt/lists/*
COPY --from=score /build/_score.so /opt/creative/_score.so

FROM cbtask:python311 AS review
RUN apt-get update && apt-get install -y --no-install-recommends iproute2 && rm -rf /var/lib/apt/lists/*
COPY --from=score /build/_score.so /opt/creative/_score.so
COPY creative_review_server.py /opt/creative/creative_review_server.py
