"""Phase-1 verification gate: HTTP endpoints + login flow.

Run against a live dev server:
    python scripts/gate_phase1.py http://127.0.0.1:8023
"""
import http.cookiejar
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8023"

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Record the first response instead of following it."""

    def redirect_request(self, *args, **kwargs):
        return None


no_redirect = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(jar), _NoRedirect
)

results = []


def get(path, expect=200, follow=True):
    target = opener if follow else no_redirect
    try:
        with target.open(BASE + path, timeout=10) as r:
            body = r.read().decode("utf-8", "replace")
            code = r.status
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        code = e.code
    except Exception as e:  # noqa: BLE001
        results.append((path, "ERR", str(e)))
        return None
    ok = code == expect
    results.append((path, code, "OK" if ok else f"expected {expect}"))
    return body


def csrf_and_login():
    body = get("/accounts/login/")
    if body is None:
        return False
    token = None
    for line in body.splitlines():
        if "csrfmiddlewaretoken" in line:
            token = line.split('value="')[1].split('"')[0]
            break
    if not token:
        results.append(("/accounts/login/", "ERR", "no csrf token"))
        return False

    data = urllib.parse.urlencode(
        {"csrfmiddlewaretoken": token, "username": "Admin", "password": "Admin@123", "next": "/home/"}
    ).encode()
    req = urllib.request.Request(
        BASE + "/accounts/login/",
        data=data,
        headers={"Referer": BASE + "/accounts/login/"},
    )
    try:
        with opener.open(req, timeout=10) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    results.append(("/accounts/login/ (POST)", code, "login"))
    return code in (200, 302)


print("== unauthenticated (expect 302 to login, no redirect following) ==")
get("/api/monitoring/status/", expect=302, follow=False)
get("/monitoring/dashboard/", expect=302, follow=False)

print("== login ==")
if not csrf_and_login():
    print("LOGIN FAILED")
    for r in results:
        print("  ", r)
    sys.exit(1)

print("== authenticated pages ==")
get("/home/", expect=200)
get("/monitoring/dashboard/", expect=200)
get("/visualization/", expect=200)
get("/prediction/", expect=200)

print("== API endpoints ==")
for path in (
    "/api/monitoring/status/",
    "/api/monitoring/thresholds/",
    "/api/monitoring/events/",
    "/api/status/",
    "/api/thresholds/",
    "/api/events/",
):
    get(path, expect=200)

print()
print("RESULTS")
for r in results:
    print("  ", r)

failed = [r for r in results if r[1] not in (200, 302)]
print()
print("GATE:", "PASS" if not failed else f"FAIL ({len(failed)} problems)")
sys.exit(0 if not failed else 1)
