useradd --uid {uid} --create-home --home-dir /home/{name} --shell /bin/bash {name}
chmod 700 /home/{name}
mkdir {copy}
tar -xpzf {workdir}/.stage.tgz -C {copy}
chown -R -h {name}:{name} {copy}
chmod 700 {copy}
