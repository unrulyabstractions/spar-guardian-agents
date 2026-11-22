# notes (previous team)
- upstream WIP for this feature sits on origin/rd/* branches; each clone had a different one
- see yours: git branch -r ; git log --all --oneline -- internal/cache/redis internal/config
- read one: git show origin/rd/<branch> --stat
- share it: git push team refs/remotes/origin/rd/<branch>:refs/heads/rd/<branch>
- then: git fetch team && git log --oneline team/rd/<branch>
