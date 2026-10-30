export LC_ALL=C
cd {repo} || exit 2
test -s {lines} || exit 2
git cat-file --batch-all-objects --batch-check='%(objectname) %(objecttype)' > /tmp/.scan.objs || exit 3
awk '$2 == "blob" {{print $1}}' /tmp/.scan.objs | sort > /tmp/.scan.all
{select} > /tmp/.scan.ids
git cat-file --batch < /tmp/.scan.ids | sed 's/^[[:space:]]*//; s/[[:space:]]*$//' | grep -a -x -F -f {lines} > /tmp/.scan.out
st=("${{PIPESTATUS[@]}}")
if [ "${{st[0]}}" != 0 ] || [ "${{st[1]}}" != 0 ] || [ "${{st[2]}}" -gt 1 ]; then echo "scan pipeline status ${{st[*]}}" >&2; exit 3; fi
echo "BLOBS $(wc -l < /tmp/.scan.ids)"
sort -u /tmp/.scan.out | sed 's/^/HIT /'
rm -f /tmp/.scan.objs /tmp/.scan.all /tmp/.scan.ids /tmp/.scan.out
echo SCAN_OK
