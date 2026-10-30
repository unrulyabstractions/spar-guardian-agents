commit={commit}
blob=$(git -C {copy} rev-parse "$commit:{archive}")
size=$(git -C {copy} cat-file -s "$blob")
git -C {copy} cat-file blob "$blob" > /tmp/.split.archive
GIT_INDEX_FILE=/tmp/.split.index git -C {copy} read-tree "$commit"
GIT_INDEX_FILE=/tmp/.split.index git -C {copy} update-index --force-remove {archive}
{slices}rm -f /tmp/.split.archive
tree=$(GIT_INDEX_FILE=/tmp/.split.index git -C {copy} write-tree)
rm -f /tmp/.split.index
git -C {copy} cat-file commit "$commit" | sed '1,/^$/d' > /tmp/.split.msg
rewritten=$(GIT_AUTHOR_NAME="$(git -C {copy} show -s --format=%an "$commit")" \
  GIT_AUTHOR_EMAIL="$(git -C {copy} show -s --format=%ae "$commit")" \
  GIT_AUTHOR_DATE="$(git -C {copy} show -s --format=%ad --date=raw "$commit")" \
  GIT_COMMITTER_NAME="$(git -C {copy} show -s --format=%cn "$commit")" \
  GIT_COMMITTER_EMAIL="$(git -C {copy} show -s --format=%ce "$commit")" \
  GIT_COMMITTER_DATE="$(git -C {copy} show -s --format=%cd --date=raw "$commit")" \
  git -C {copy} commit-tree "$tree" -p "$commit^" -F /tmp/.split.msg)
rm -f /tmp/.split.msg
git -C {copy} update-ref refs/heads/{branch} "$rewritten"
git -C {copy} update-ref refs/remotes/origin/{branch} "$rewritten"
