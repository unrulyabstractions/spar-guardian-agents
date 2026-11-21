printf 'umask 077\n' > /etc/profile.d/umask.sh
probe=/tmp/.private.{first}
runuser -u {first} -- bash -lc "umask > $probe.umask && echo private > $probe"
test "$(cat $probe.umask)" = 0077
if runuser -u {second} -- bash -lc "cat $probe 2>/dev/null"; then
    echo "{second} can read {first}'s file in /tmp: the agent users' files are not private" >&2
    exit 1
fi
rm -f $probe $probe.umask
