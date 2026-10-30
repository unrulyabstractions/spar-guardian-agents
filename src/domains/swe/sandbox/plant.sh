set -e
cd {workdir}
export GIT_INDEX_FILE=/tmp/.plant.idx
git read-tree {base}
git apply --cached /tmp/.plant.patch
tree=$(git write-tree)
commit=$({env} git commit-tree $tree -p {base} -m {message})
git update-ref refs/remotes/origin/{branch} $commit
rm -f /tmp/.plant.idx /tmp/.plant.patch
echo $commit
