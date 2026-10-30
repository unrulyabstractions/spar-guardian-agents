useradd --uid {uid} --create-home --home-dir /home/{name} --shell /bin/bash {name}
chmod 700 /home/{name}
mkdir {workdir}/{name}
tar -xpzf {workdir}/.stage.tgz -C {workdir}/{name}
chown -R -h {name}:{name} {workdir}/{name}
chmod 700 {workdir}/{name}
