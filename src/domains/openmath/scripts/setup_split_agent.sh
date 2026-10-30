useradd --uid {uid} --create-home --home-dir /home/{name} --shell /bin/bash --groups {group} {name}
chmod 700 /home/{name}
mkdir {copy}
tar -xpzf {workdir}/.stage.tgz --no-same-owner -C {copy}
rm -rf {copy}/.git/internal.git {copy}/.git/FETCH_HEAD {copy}/.git/ORIG_HEAD
git -C {copy} remote set-url origin {remote}
{rewrite}git -C {copy} reflog expire --expire=now --all
git -C {copy} prune --expire=now
chown -R -h {name}:{name} {copy}
chmod 700 {copy}
