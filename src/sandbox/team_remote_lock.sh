# Run as root on a new bare team remote: the group pushes to it, and no agent decides what git runs in it. The top
# directory stays group-writable, since git locks HEAD there, so the sticky bit keeps an agent from renaming or deleting
# what root owns there: hooks/ and config, which the group cannot write; commondir, which holds this directory, so an
# agent cannot point git at a config of its own; and an empty .git/, which git would enter ahead of the remote itself.
chgrp -R {group} {remote}
chmod -R g+rwX {remote}
find {remote} -type d -exec chmod g+s {{}} +
chmod -R g-w {remote}/hooks {remote}/config
printf '.\n' > {remote}/commondir
chmod 644 {remote}/commondir
mkdir -m 755 {remote}/.git
chmod +t {remote}
