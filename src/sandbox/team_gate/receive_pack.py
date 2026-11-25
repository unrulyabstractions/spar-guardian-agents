"""git-receive-pack on a machine kept out of the team remote's main: what `git push` runs for a local path.

A push to the team remote goes through the gate's socket (gate_server.py); a push to any other path runs git's own
receive-pack. The machine mounts the remote read-only, so this program is the only way its pushes land, and changing
it only loses the gate. REMOTE and SOCKET are filled in when it is installed (team_gate.install_script).
"""
import os
import socket
import sys
import threading

REMOTE = "{remote}"
SOCKET = "{socket}"


def is_remote(path: str) -> bool:
    try:
        return os.path.realpath(path) == os.path.realpath(REMOTE)
    except OSError:
        return False


def relay() -> int:
    """stdin to the gate and the gate to stdout, until the gate closes the connection."""
    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        conn.connect(SOCKET)
    except OSError:
        print("fatal: Could not read from remote repository.", file=sys.stderr)
        return 128

    def send() -> None:
        try:
            while chunk := os.read(0, 65536):
                conn.sendall(chunk)
            conn.shutdown(socket.SHUT_WR)
        except OSError:
            pass

    threading.Thread(target=send, daemon=True).start()
    while chunk := conn.recv(65536):
        os.write(1, chunk)
    return 0


def main(args: list[str]) -> int:
    paths = [a for a in args if not a.startswith("-")]
    if len(paths) == 1 and len(args) == 1 and is_remote(paths[0]):
        return relay()
    os.execvp("git", ["git", "receive-pack", *args])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
