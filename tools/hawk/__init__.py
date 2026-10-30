"""The hawk tool: our Inspect Scout scanners run by Hawk (METR's eval platform) as a local scan.

`python -m tools hawk <run>...` writes runs/<name>/tools/hawk/scan.yaml for each run and prints the
`hawk local scan` command; `--run` runs it in that directory, so Hawk's results land under results/. The scan reads
the run's per-agent Scout transcripts database (runs/<name>/tools/scout/transcripts, else one built under
tools/hawk/transcripts), or with `--source eval_log` the eval log's directory. Hawk reaches our scanners through the
shim distribution in scanner_package/.

summary.json, beside tool, run and written_at:
    config: str             the scan.yaml path
    command: str            the hawk command line, run from cwd
    cwd: str                runs/<name>/tools/hawk
    ran: bool               whether --run ran it
    returncode: int | None  hawk's exit code (124 on our timeout); None when not run
    stdout_tail: [str]      the last lines of hawk's stdout and stderr
    needs_server: bool      false: a local scan needs no HAWK_API_URL and no login
    notes: [str]            what works locally and what needs a deployed Hawk
    source: {kind, path}    kind scout_db, hawk_db or eval_log; path the transcripts directory
    scanners: [str]         the scanner items
    model: str              the scan's Inspect model string
    results: [str]          the scan directories this invocation wrote (results/local-scan-<job>/scan_id=<id>)
    complete: bool | None   whether every written scan's _summary.json says complete; None when not run
    scan_errors: int        the scanner errors summed over those _summary.json files

With --run the command exits 1 unless hawk exits 0, writes a scan directory, the scan is complete and has no errors.
"""
