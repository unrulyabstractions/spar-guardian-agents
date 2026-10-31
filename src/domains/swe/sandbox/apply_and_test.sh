set -u
cd {workdir}
git reset -q --hard {base} && git clean -ffdxq
if [ -s {diff} ]; then git apply --binary --whitespace=nowarn {diff} || {{ echo APPLY_FAILED; exit 0; }}; fi
{add}
rm -rf /tests /logs/verifier && mkdir -p /logs/verifier && cp -r /tmp/grade/tests /tests
bash /tests/test.sh > /tmp/grade/{name}.out 2>&1
echo "TEST_RC=$?"
echo "REWARD=$(cat /logs/verifier/reward.txt 2>/dev/null)"
python3 - <<'PY'
import json
try:
    d = json.load(open("/logs/verifier/output.json"))
    print("PASSED=" + json.dumps(sorted(t.get("name", "") for t in d.get("tests", []) if t.get("status") == "PASSED")))
except Exception as e:
    print("PASSED=null")
PY
tail -c 1500 /tmp/grade/{name}.out | sed 's/^/OUT| /'
