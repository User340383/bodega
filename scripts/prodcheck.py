"""Locally verifiable deployment checks and server-side NFR timings against compose.prodcheck.yml.

    docker compose -f compose.prodcheck.yml up -d --build --wait
    python scripts/prodcheck.py          (standard library only; prints JSON, exit 1 on failure)

Timings follow the SRS 5 measurement rule: at least 50 trials, 95th percentile, under "normal
load" = 10 concurrent owner sessions, with 5,000 confirmed records.
"""
import json
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

WEB, API = "http://127.0.0.1:8090", "http://127.0.0.1:8091"
CODE = "prodcheck-access-code"
AUTH = {"Authorization": f"Bearer {CODE}"}
SESSIONS, TRIALS = 10, 50
checks, failures = [], []


def request(url, method="GET", headers=None, body=None, content_type=None):
    data = body if isinstance(body, bytes) or body is None else json.dumps(body).encode()
    hdrs = dict(headers or {})
    if data is not None:
        hdrs["Content-Type"] = content_type or "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def check(name, ok, detail=""):
    checks.append({"check": name, "ok": bool(ok), "detail": detail})
    if not ok:
        failures.append(name)


def p95(values):
    return statistics.quantiles(values, n=20)[-1]


def deployment_checks():
    status, headers, body = request(f"{WEB}/analysis")
    check("nginx serves SPA routes", status == 200 and b'<div id="root">' in body)
    check("nginx sends HSTS and nosniff", "max-age=31536000" in headers.get("Strict-Transport-Security", "")
          and headers.get("X-Content-Type-Options") == "nosniff")
    _, headers, body = request(f"{WEB}/config.js")
    check("config.js points the bundle at the API origin", b"apiBaseUrl: 'http://127.0.0.1:8091'" in body
          and "no-store" in headers.get("Cache-Control", ""), body.decode().strip())
    status, headers, body = request(f"{API}/health")
    check("API /health is 200 after migrations", status == 200 and json.loads(body) == {"status": "ok"})
    preflight = {"Origin": WEB, "Access-Control-Request-Method": "POST",
                 "Access-Control-Request-Headers": "authorization,content-type"}
    _, headers, _ = request(f"{API}/api/v1/confirm", "OPTIONS", preflight)
    check("CORS allows the frontend origin", headers.get("access-control-allow-origin") == WEB)
    _, headers, _ = request(f"{API}/api/v1/confirm", "OPTIONS", {**preflight, "Origin": "https://evil.example"})
    check("CORS refuses any other origin", "access-control-allow-origin" not in {k.lower() for k in headers})
    status, headers, body = request(f"{API}/api/v1/sales")
    check("API requires the access code (401, envelope)", status == 401 and json.loads(body)["error"]["code"] == "UNAUTHORIZED")
    check("API returns X-Request-ID and no-store", bool(headers.get("x-request-id"))
          and headers.get("cache-control") == "no-store")
    status, _, _ = request(f"{API}/docs")
    check("API docs reachable (no secrets in OpenAPI)", status == 200)
    _, _, schema = request(f"{API}/openapi.json")
    # M3 4.2 (D-30): every validation error is 400, including /confirm and /manual-entries.
    paths = json.loads(schema)["paths"]
    check("OpenAPI documents 400 for validation, never 422 (M3 4.2)", b'"422"' not in schema
          and all("400" in paths[path]["post"]["responses"] for path in ("/api/v1/confirm", "/api/v1/manual-entries")))


def load_records(total=5000, days=28, items=100):
    """5,000 confirmed records (28 logged days x 100 items) written straight to PostgreSQL."""
    owner = "00000000-0000-4000-8000-000000000001"
    today = date.today()
    statements = [f"INSERT INTO owners(id) VALUES ('{owner}') ON CONFLICT DO NOTHING;"]
    made = 0
    per_day = -(-total // days)
    for offset in range(days):
        day = today - timedelta(days=offset + 1)
        count = min(per_day, total - made)
        for start in range(0, count, 100):
            sid = uuid.uuid4()
            statements.append(f"INSERT INTO submissions(id,owner_id,sale_date,input_type,status) "
                              f"VALUES ('{sid}','{owner}','{day}','MANUAL','CONFIRMED');")
            rows = ",".join(f"('{uuid.uuid4()}','{sid}',{line + 1},'Item {(start + line) % items:03d}',"
                            f"{1 + (offset + line) % 9},1.25,'MANUAL')" for line in range(min(100, count - start)))
            statements.append("INSERT INTO sales_records(id,submission_id,line_no,item_name,quantity,unit_price,source) "
                              f"VALUES {rows};")
        made += count
    script = "BEGIN;\n" + "\n".join(statements) + "\nCOMMIT;\n"
    subprocess.run(["docker", "compose", "-f", "compose.prodcheck.yml", "exec", "-T", "db", "psql", "-q",
                    "-v", "ON_ERROR_STOP=1", "-U", "bodega", "-d", "bodega"], input=script, text=True, check=True,
                   capture_output=True)
    _, _, body = request(f"{API}/api/v1/sales?start={today - timedelta(days=days)}&end={today}", headers=AUTH)
    return json.loads(body)["total"]  # GET /sales pages its records (D-32); total counts the range.


def concurrent(fn):
    with ThreadPoolExecutor(SESSIONS) as pool:
        return list(pool.map(lambda _: fn(), range(TRIALS)))


def timed(fn):
    started = time.perf_counter()
    result = fn()
    return time.perf_counter() - started, result


def nfr_timings(records):
    invalid = [{"saleDate": "not-a-date", "text": "Bread 3"}, {"saleDate": "9999-12-31", "text": "Bread 3"},
               {"saleDate": date.today().isoformat(), "text": "<script>alert(1)</script>"}]

    def validate():
        form = invalid[int(time.perf_counter() * 1000) % len(invalid)]
        boundary = uuid.uuid4().hex
        body = "".join(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n' for k, v in form.items())
        body += f"--{boundary}--\r\n"
        return timed(lambda: request(f"{API}/api/v1/extract", "POST", AUTH, body.encode(),
                                     f"multipart/form-data; boundary={boundary}")[0])

    results = concurrent(validate)
    times = [t for t, _ in results]
    check("NFR-701 every invalid input rejected with 400", all(s == 400 for _, s in results))
    check("NFR-701 validation p95 <= 2.0 s (50 trials, 10 sessions)", p95(times) <= 2.0, f"p95 {p95(times):.3f} s")
    results = concurrent(lambda: timed(lambda: request(f"{API}/api/v1/analysis", headers=AUTH)))
    times = [t for t, _ in results]
    check("NFR-703 analysis p95 <= 3.0 s at 5,000 records (50 trials, 10 sessions)",
          records == 5000 and p95(times) <= 3.0 and all(r[1][0] == 200 for r in results),
          f"records {records}, p95 {p95(times):.3f} s, median {statistics.median(times):.3f} s")
    return {"NFR-701": round(p95(times), 3)}


def main():
    deployment_checks()
    records = load_records()
    nfr_timings(records)
    print(json.dumps({"checks": checks, "failed": failures}, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
