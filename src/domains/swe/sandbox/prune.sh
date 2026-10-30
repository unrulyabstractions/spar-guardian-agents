set -e
cd {workdir}
cur=$(git symbolic-ref -q HEAD || echo none)
git reflog expire --expire=now --all
for c in $(git log --all --format=%H -G {mark} ^{base} -- {paths}); do git for-each-ref --format='%(refname)' --contains $c; done \
  | sort -u | grep -vx "$cur" > /tmp/.prune.refs || true
while read -r r; do git update-ref --no-deref -d "$r" || true; done < /tmp/.prune.refs
git stash clear; rm -f .git/ORIG_HEAD .git/FETCH_HEAD
git reflog expire --expire=now --all
git gc -q --prune=now
echo "deleted $(wc -l < /tmp/.prune.refs) refs; left with the fix: $(git log --all --format=%H -G {mark} ^{base} -- {paths} | wc -l)"
rm -f /tmp/.prune.refs
git cat-file -e {fix}^{{commit}} 2>/dev/null && echo "fix commit still present" || echo "fix commit gone"
