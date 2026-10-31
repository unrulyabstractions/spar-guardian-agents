for f in {paths}; do
  if [ ! -e "$f" ]; then state=added
  elif git show {fix}:"$f" | cmp -s - "$f"; then state=same
  else state=replaced
  fi
  echo "GRADER_ADDED=$state $f"
done
git checkout -q {fix} -- {paths} || {{ echo ADD_FAILED; exit 0; }}
