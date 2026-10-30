chunk=$((size / {parts}))
start=$((({part} - 1) * chunk))
if [ {part} -eq {parts} ]; then len=$((size - start)); else len=$chunk; fi
slice=$(head -c $((start + len)) /tmp/.split.archive | tail -c "$len" | git -C {copy} hash-object -w --stdin)
GIT_INDEX_FILE=/tmp/.split.index git -C {copy} update-index --add --cacheinfo "100644,$slice,{archive}.part{part}"
