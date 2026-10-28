#!/usr/bin/env bash
# Prove that every output file on a rented box has a byte-identical-size local copy before the box is destroyed.
#
#     scripts/box/capture_check.sh <ssh-wrapper> <remote-repo-root> [<local-repo-root>]
#
# The wrapper runs `ssh ... "$@"` against the box. The script sweeps every regular file under the remote repo's
# runs/, out/ and logs/ (no name filter), compares each size with the local file at the same path, and prints every
# file that is missing locally or differs in size. Exit 0 only when nothing is missing; the destroy step is gated on it.
set -uo pipefail
SSH="$1"; REMOTE="$2"; LOCAL="${3:-$(pwd)}"
listing=$(mktemp)
"$SSH" "cd '$REMOTE' && find runs out logs -type f -printf '%s\t%p\n' 2>/dev/null" > "$listing" || { echo "listing failed"; exit 2; }
total=0; missing=0; differ=0
while IFS=$'\t' read -r size path; do
  [ -z "$path" ] && continue
  total=$((total+1))
  if [ ! -f "$LOCAL/$path" ]; then echo "MISSING  $path ($size bytes)"; missing=$((missing+1)); continue; fi
  local_size=$(stat -f %z "$LOCAL/$path" 2>/dev/null || stat -c %s "$LOCAL/$path")
  if [ "$local_size" != "$size" ]; then echo "DIFFERS  $path (remote $size, local $local_size)"; differ=$((differ+1)); fi
done < "$listing"
rm -f "$listing"
echo "swept $total files: $missing missing, $differ differ"
[ "$missing" -eq 0 ] && [ "$differ" -eq 0 ]
