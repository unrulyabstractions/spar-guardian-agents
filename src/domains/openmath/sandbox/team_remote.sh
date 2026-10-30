groupadd {group}
mkdir -m 750 {team}
chgrp {group} {team}
mkdir {stage}
tar -xpzf {workdir}/.stage.tgz --no-same-owner -C {stage}
git init -q --bare --shared=group --initial-branch=main {remote}
git --git-dir={stage}/.git/internal.git push -q {remote} {refs}
rm -rf {stage}
chgrp -R {group} {remote}
chmod -R g+rwX {remote}
find {remote} -type d -exec chmod g+s {{}} +
chmod -R g-w {remote}/hooks {remote}/config
git config --system --add safe.directory {remote}
