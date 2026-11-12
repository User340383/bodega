"""NFR-725: 0 lost confirmed records and 0 partial drafts across 10 forced backend restarts
performed during confirmation (FR-302).

Runs entirely in its own Compose project (compose.test.yml, tmpfs PostgreSQL); no other
container or volume is touched. Each trial:
  1. creates an open submission through the real API (no AI provider, so AI_UNAVAILABLE);
  2. installs a test-only trigger that sleeps inside each sales-row INSERT, so the
     confirmation transaction is guaranteed to be open (rows partly inserted, uncommitted);
  3. starts POST /api/v1/confirm (3 rows) from a separate client container;
  4. SIGKILLs the backend container while that transaction is open, then starts it again;
  5. audits the database: the submission must still be open with 0 rows, no CONFIRMED
     submission may be missing rows, and every record confirmed in earlier trials must remain;
  6. removes the trigger and confirms the same submission normally (201, 3 rows).

    python scripts/nfr725_restart_trial.py        (from the repository root, Docker running)
"""
import json
import subprocess
import sys
import time

PROJECT = "bodega-restart-trial"
COMPOSE = ["docker", "compose", "-p", PROJECT, "-f", "compose.test.yml", "--profile", "smoke"]
TRIALS = 10
CODE = "smoke-test-code"
API = "http://backend-smoke:8000"


def run(args, check=True, capture=True):
    result = subprocess.run(args, capture_output=capture, text=True)
    if check and result.returncode:
        sys.exit(f"command failed: {' '.join(args)}\n{result.stdout}\n{result.stderr}")
    return result.stdout.strip() if capture else ""


def sql(statement):
    return run(COMPOSE + ["exec", "-T", "postgres-test", "psql", "-v", "ON_ERROR_STOP=1", "-U", "bodega_test",
                          "-d", "bodega_test", "-At", "-c", statement])


def client(code, detach=False):
    """Run Python in a throwaway client container on the project network (it survives the kill)."""
    args = COMPOSE + ["run", "--rm", "--no-deps", "-T"] + (["-d"] if detach else []) + [
        "-e", f"API={API}", "-e", f"CODE={CODE}", "http-smoke", "python", "-c", code]
    return run(args)


CREATE = """
import httpx, os, datetime
r = httpx.post(os.environ['API'] + '/api/v1/extract', headers={'Authorization': 'Bearer ' + os.environ['CODE']},
               data={'saleDate': datetime.date.today().isoformat(), 'text': 'Bread 3 1.50'}, timeout=30)
r.raise_for_status(); print(r.json()['submissionId'])
"""
CONFIRM = """
import httpx, os, sys
body = {'submissionId': sys.argv[1] if len(sys.argv) > 1 else os.environ['SID'], 'lines': [
    {'itemName': 'Bread', 'quantity': '3', 'unitPrice': '1.50'}, {'itemName': 'Milk', 'quantity': '2', 'unitPrice': '1.20'},
    {'itemName': 'Eggs', 'quantity': '12', 'unitPrice': '0.25'}]}
try:
    r = httpx.post(os.environ['API'] + '/api/v1/confirm', headers={'Authorization': 'Bearer ' + os.environ['CODE']},
                   json=body, timeout=60)
    print(r.status_code, r.json().get('created'))
except httpx.HTTPError as exc:
    print('connection lost:', type(exc).__name__)
"""


def wait_healthy(service, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        cid = run(COMPOSE + ["ps", "-q", service])
        if cid and run(["docker", "inspect", "-f", "{{.State.Health.Status}}", cid], check=False) == "healthy":
            return cid
        time.sleep(1)
    sys.exit(f"{service} did not become healthy")


def main():
    run(COMPOSE + ["up", "-d", "--build", "--wait", "postgres-test", "backend-smoke"], capture=False)
    results, confirmed_before = [], 0
    try:
        for trial in range(1, TRIALS + 1):
            sid = client(CREATE).splitlines()[-1]
            sql("""CREATE OR REPLACE FUNCTION trial_hold() RETURNS trigger LANGUAGE plpgsql AS $$
                   BEGIN PERFORM pg_sleep(2); RETURN NEW; END $$;
                   DROP TRIGGER IF EXISTS trial_hold ON sales_records;
                   CREATE TRIGGER trial_hold BEFORE INSERT ON sales_records FOR EACH ROW EXECUTE FUNCTION trial_hold();""")
            client(CONFIRM.replace("os.environ['SID']", repr(sid)), detach=True)
            # Wait until the confirmation transaction is open inside an INSERT, then kill the backend.
            deadline = time.time() + 30
            while time.time() < deadline and sql(
                    "SELECT count(*) FROM pg_stat_activity WHERE query LIKE 'INSERT INTO sales_records%' AND state='active'") == "0":
                time.sleep(0.2)
            in_flight = sql("SELECT count(*) FROM pg_stat_activity WHERE query LIKE 'INSERT INTO sales_records%' AND state='active'")
            backend = run(COMPOSE + ["ps", "-q", "backend-smoke"])
            run(["docker", "kill", "--signal", "KILL", backend])
            killed_at = time.time()
            run(COMPOSE + ["start", "backend-smoke"])
            wait_healthy("backend-smoke")
            # The orphaned server session rolls back when it finds its client gone; wait for it.
            while sql("SELECT count(*) FROM pg_stat_activity WHERE query LIKE 'INSERT INTO sales_records%'") != "0":
                time.sleep(0.5)
            sql("DROP TRIGGER trial_hold ON sales_records")
            state = sql(f"SELECT status || '|' || (SELECT count(*) FROM sales_records WHERE submission_id=s.id) "
                        f"FROM submissions s WHERE id='{sid}'")
            partial = sql("SELECT count(*) FROM submissions s WHERE (status='CONFIRMED') <> "
                          "EXISTS (SELECT 1 FROM sales_records r WHERE r.submission_id = s.id)")
            confirmed_rows = int(sql("SELECT count(*) FROM sales_records"))
            redo = client(CONFIRM.replace("os.environ['SID']", repr(sid))).splitlines()[-1]
            after = int(sql("SELECT count(*) FROM sales_records"))
            ok = (in_flight != "0" and state == "AI_UNAVAILABLE|0" and partial == "0"
                  and confirmed_rows == confirmed_before and redo == "201 True" and after == confirmed_before + 3)
            results.append({"trial": trial, "killedWhileInsertActive": in_flight != "0", "stateAfterRestart": state,
                            "inconsistentSubmissions": int(partial), "recordsBefore": confirmed_before,
                            "recordsAfterRestart": confirmed_rows, "reconfirm": redo, "recordsAfterReconfirm": after,
                            "restartSeconds": round(time.time() - killed_at, 1), "passed": ok})
            confirmed_before = after
            print(json.dumps(results[-1]))
    finally:
        run(COMPOSE + ["down"], check=False)
    passed = sum(r["passed"] for r in results)
    print(json.dumps({"NFR-725": {"trials": len(results), "passed": passed,
                                  "lostConfirmedRecords": 0 if passed == TRIALS else "see trials",
                                  "result": "PASS" if passed == TRIALS else "FAIL"}}))
    return 0 if passed == TRIALS else 1


if __name__ == "__main__":
    sys.exit(main())
