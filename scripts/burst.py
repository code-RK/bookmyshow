#!/usr/bin/env python3
"""On-sale stampede against a running BookMyShow service.

    python scripts/burst.py http://localhost:8000
    python scripts/burst.py https://your-app.example.com --requests 20000

Standard library only, so it runs anywhere with Python 3.9+. Each run creates a
fresh show, so runs never interfere with each other. Phases:

  1. setup      admin login (ADMIN_USERNAME / ADMIN_PASSWORD), create a show,
                log in (or register) a pool of test users
  2. stampede   everything at once, shuffled together:
                  - hot-seat storm: a large share of all requests aims at a
                    handful of seats
                  - general on-sale traffic for 1-2 seats each
                  - retries that resend an earlier request with the same key
                  - one user firing 10 parallel requests at a limit-4 show
                  - requests carrying someone else's user_id in the body
  3. idempotency  a confirmed key resent with the same body (-> replay) and
                  with different seats (-> 409)
  4. release    hot-seat winners cancel (while another user tries to cancel
                the same reservations), then the freed seats are stormed again
  5. reconcile  outcome distribution plus checks: one winner per contested
                seat, no seat sold twice, per-user limit held, retries booked
                nothing extra, zero 5xx, available + held + confirmed ==
                total_seats, and /metrics agrees with the API

Exits with status 1 if any check fails.
"""

import argparse
import base64
import collections
import http.client
import json
import os
import random
import ssl
import statistics
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

DEFAULT_USER_PASSWORD = "Seat-Storm-Pass-2026!"


# --------------------------------------------------------------------- HTTP

class Client:
    """Keep-alive HTTP client with one connection per thread."""

    # Servers drop idle keep-alive connections (gunicorn after 5s); reconnect
    # before reusing one that has been idle this long.
    MAX_IDLE = 3.0

    def __init__(self, base_url, api_prefix, timeout):
        parts = urlsplit(base_url.rstrip("/"))
        if parts.scheme not in ("http", "https"):
            sys.exit(f"BASE_URL must start with http:// or https:// (got {base_url!r})")
        self.scheme = parts.scheme
        self.host = parts.hostname
        self.port = parts.port
        self.root = parts.path
        self.api = parts.path + api_prefix
        self.timeout = timeout
        self.local = threading.local()
        self.ssl_context = ssl.create_default_context()

    def _connection(self, fresh=False):
        conn = getattr(self.local, "conn", None)
        if conn is not None and time.monotonic() - self.local.last > self.MAX_IDLE:
            fresh = True
        if conn is None or fresh:
            if conn is not None:
                conn.close()
            if self.scheme == "https":
                conn = http.client.HTTPSConnection(self.host, self.port, timeout=self.timeout,
                                                   context=self.ssl_context)
            else:
                conn = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
            self.local.conn = conn
            self.local.used = False
            self.local.last = time.monotonic()
        return conn

    def request(self, method, path, body=None, token=None, headers=None, root=False, text=False):
        """Returns (status, response, parsed JSON or None, seconds).

        With text=True the third item is the body as a string instead.

        status is an int, or a string like "ERR ConnectionResetError" when no
        HTTP response arrived.
        """
        url = (self.root if root else self.api) + path
        hdrs = {"Accept": "application/json"}
        if body is not None:
            hdrs["Content-Type"] = "application/json"
        if token:
            hdrs["Authorization"] = f"Bearer {token}"
        hdrs.update(headers or {})
        payload = json.dumps(body).encode() if body is not None else None

        start = time.perf_counter()
        for attempt in (1, 2):
            conn = self._connection()
            reused = self.local.used
            try:
                conn.request(method, url, body=payload, headers=hdrs)
                resp = conn.getresponse()
                raw = resp.read()
                self.local.used = True
                self.local.last = time.monotonic()
                if resp.getheader("Connection", "").lower() == "close":
                    self._connection(fresh=True)
                if text:
                    data = raw.decode("utf-8", "replace")
                else:
                    try:
                        data = json.loads(raw) if raw else None
                    except ValueError:
                        data = None
                return resp.status, resp, data, time.perf_counter() - start
            except (http.client.RemoteDisconnected, ConnectionError) as exc:
                # The server closed an idle keep-alive connection just as we
                # reused it; the request never reached the app, so resending
                # once on a new connection is safe (and reserve requests carry
                # an idempotency key anyway).
                self._connection(fresh=True)
                if attempt == 2 or not reused:
                    return f"ERR {type(exc).__name__}", None, None, time.perf_counter() - start
            except (OSError, http.client.HTTPException) as exc:
                self._connection(fresh=True)
                return f"ERR {type(exc).__name__}", None, None, time.perf_counter() - start


def token_user_id(token):
    """user_id claim of a SimpleJWT access token (read only, not verified)."""
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    # SimpleJWT stores the claim as a string; the API returns ids as numbers.
    return int(json.loads(base64.urlsafe_b64decode(payload))["user_id"])


# ------------------------------------------------------------------ results

class Result:
    __slots__ = ("phase", "kind", "status", "reason", "replayed", "body", "seconds",
                 "user", "key", "seats", "check")

    def __init__(self, phase, kind, status, resp, body, seconds, user, key, seats, check=None):
        self.phase, self.kind, self.status = phase, kind, status
        self.body, self.seconds, self.user, self.key, self.seats = body, seconds, user, key, seats
        self.replayed = bool(resp is not None and resp.getheader("Idempotent-Replayed") == "true")
        self.reason = body.get("reason") if isinstance(body, dict) else None
        self.check = check

    @property
    def outcome(self):
        if not isinstance(self.status, int):
            return "transport error"
        if self.status >= 500:
            return f"5xx ({self.status})"
        if self.status == 201:
            return "idempotent replay (201)" if self.replayed else "confirmed (201)"
        if self.status == 409:
            return f"declined: {self.reason or 'unknown'} (409)"
        return f"other ({self.status})"


class Checks:
    def __init__(self):
        self.failed = []

    def __call__(self, label, ok, detail=""):
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))
        if not ok:
            self.failed.append(label)


def run_parallel(fn, jobs, concurrency, label):
    """Run fn over jobs with a live progress line; returns results in order."""
    done = [0]
    lock = threading.Lock()
    stop = threading.Event()
    start = time.perf_counter()

    def wrapped(job):
        r = fn(job)
        with lock:
            done[0] += 1
        return r

    def progress():
        while not stop.wait(2):
            el = time.perf_counter() - start
            print(f"    {label}: {done[0]}/{len(jobs)} ({done[0] / el:.0f} req/s)", flush=True)

    t = threading.Thread(target=progress, daemon=True)
    t.start()
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        results = list(ex.map(wrapped, jobs))
    stop.set()
    return results, time.perf_counter() - start


def print_distribution(results, elapsed):
    counts = collections.Counter(r.outcome for r in results)
    order = sorted(counts, key=lambda o: (not o.startswith("confirmed"), not o.startswith("idempotent"),
                                          not o.startswith("declined"), o))
    width = max(len(o) for o in order)
    for o in order:
        print(f"    {o:<{width}}  {counts[o]:>7}  {100 * counts[o] / len(results):5.1f}%")
    lat = sorted(r.seconds * 1000 for r in results)
    pct = lambda q: lat[min(len(lat) - 1, int(q * len(lat)))]
    print(f"    {'total':<{width}}  {len(results):>7}  in {elapsed:.1f}s = {len(results) / elapsed:.0f} req/s")
    print(f"    latency ms: p50 {pct(.50):.0f}  p95 {pct(.95):.0f}  p99 {pct(.99):.0f}  max {lat[-1]:.0f}")


# --------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("base_url", help="service root, e.g. http://localhost:8000")
    ap.add_argument("--requests", type=int, default=20000, help="stampede size (default 20000)")
    ap.add_argument("--concurrency", type=int, default=200, help="requests in flight at once (default 200)")
    ap.add_argument("--seats", type=int, default=500, help="seats in the show (default 500)")
    ap.add_argument("--hot-seats", type=int, default=5, help="number of hot seats (default 5)")
    ap.add_argument("--hot-share", type=float, default=0.25,
                    help="share of the stampede aimed at hot seats (default 0.25)")
    ap.add_argument("--retry-share", type=float, default=0.10,
                    help="share of requests resent with the same idempotency key (default 0.10)")
    ap.add_argument("--users", type=int, default=200, help="test users (default 200)")
    ap.add_argument("--admin-user", default=os.environ.get("ADMIN_USERNAME"),
                    help="admin username (default: $ADMIN_USERNAME)")
    ap.add_argument("--admin-password", default=os.environ.get("ADMIN_PASSWORD"),
                    help="admin password (default: $ADMIN_PASSWORD)")
    ap.add_argument("--user-password", default=os.environ.get("BURST_USER_PASSWORD", DEFAULT_USER_PASSWORD),
                    help="password for the test users (default: $BURST_USER_PASSWORD or a built-in one)")
    ap.add_argument("--api-prefix", default="/api/v1")
    ap.add_argument("--timeout", type=float, default=120, help="per-request timeout in seconds")
    args = ap.parse_args()

    if not args.admin_user or not args.admin_password:
        sys.exit("Admin credentials needed to create the show: set ADMIN_USERNAME and ADMIN_PASSWORD "
                 "(or pass --admin-user / --admin-password).")
    # hot seats + per-user-limit block (10) + idempotency seats (2) + spoof seats (4)
    reserved = args.hot_seats + 10 + 2 + 4
    if args.seats < reserved + 10:
        sys.exit(f"--seats must be at least {reserved + 10}")

    client = Client(args.base_url, args.api_prefix, args.timeout)
    check = Checks()
    all_results = []
    run_id = uuid.uuid4().hex[:8]
    rng = random.Random()

    # ---------------------------------------------------------------- setup
    print(f"BookMyShow burst against {args.base_url}  (run {run_id})\n")
    print("[1/5] setup")
    status, _, body, _ = client.request("GET", "/health/ready", root=True)
    if status != 200:
        sys.exit(f"  /health/ready returned {status} {body} - is the service up?")

    status, _, body, _ = client.request("POST", "/auth/login",
                                        {"username": args.admin_user, "password": args.admin_password})
    if status != 200:
        sys.exit(f"  admin login failed ({status}): {body}")
    admin = body["access"]

    width = len(str(args.seats))
    seat_names = [f"A{i:0{width}d}" for i in range(1, args.seats + 1)]
    hot = seat_names[:args.hot_seats]
    limit_block = seat_names[args.hot_seats:args.hot_seats + 10]
    key_seats = seat_names[args.hot_seats + 10:args.hot_seats + 12]
    spoof_seats = seat_names[args.hot_seats + 12:reserved]
    pool = seat_names[reserved:]

    status, _, show, _ = client.request("POST", "/shows", {
        "name": f"burst-{run_id}", "seats": seat_names, "price_paise": 25000}, admin)
    if status == 403:
        sys.exit("  the admin login is not an admin (role must be 'admin')")
    if status != 201:
        sys.exit(f"  creating the show failed ({status}): {show}")
    show_id = show["id"]
    limit = show.get("per_user_limit", 4)
    print(f"  show {show_id}: {args.seats} seats, per_user_limit {limit}, hot seats {', '.join(hot)}")

    def login_or_register(name):
        creds = {"username": name, "password": args.user_password}
        st, _, b, _ = client.request("POST", "/auth/login", creds)
        if st == 401:
            client.request("POST", "/auth/register", creds)
            st, _, b, _ = client.request("POST", "/auth/login", creds)
        if st != 200:
            raise RuntimeError(f"login for {name} failed ({st}): {b}")
        return b["access"]

    # Logins hash a password (deliberately slow), so they happen here, before
    # the clock starts, rather than inside the stampede.
    names = [f"burst_user_{i:04d}" for i in range(args.users)] + [
        "burst_limit_user", "burst_key_user", "burst_spoof_user"]
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=16) as ex:
        tokens = list(ex.map(login_or_register, names))
    limit_token, key_token, spoof_token = tokens[-3:]
    tokens = tokens[:-3]
    spoof_user_id = token_user_id(spoof_token)
    user_ids = [token_user_id(t) for t in tokens]
    print(f"  {len(names)} users logged in ({time.perf_counter() - t0:.1f}s)")

    def reserve(phase, kind, user, seats, key, extra_body=None):
        tok = {"limit": limit_token, "key": key_token, "spoof": spoof_token}.get(
            user, tokens[user] if isinstance(user, int) else None)
        body = {"seats": seats, **(extra_body or {})}
        st, resp, b, sec = client.request("POST", f"/shows/{show_id}/reserve", body, tok,
                                          {"Idempotency-Key": key})
        return Result(phase, kind, st, resp, b, sec, user, key, seats)

    # ------------------------------------------------------------- stampede
    print(f"\n[2/5] stampede: {args.requests} requests, {args.concurrency} in flight")
    originals = int(args.requests / (1 + args.retry_share)) - 14
    hot_n = int(originals * args.hot_share)
    jobs = []
    for i in range(hot_n):
        jobs.append(("hot", rng.randrange(len(tokens)), [hot[i % len(hot)]], f"{run_id}-h{i}"))
    for i in range(originals - hot_n):
        if rng.random() < 0.2:
            j = rng.randrange(len(pool) - 1)
            seats = [pool[j], pool[j + 1]]
        else:
            seats = [rng.choice(pool)]
        jobs.append(("general", rng.randrange(len(tokens)), seats, f"{run_id}-g{i}"))
    retries = [("retry",) + job[1:] for job in rng.sample(jobs, int(len(jobs) * args.retry_share))]
    jobs += retries
    jobs += [("limit", "limit", [s], f"{run_id}-l{i}") for i, s in enumerate(limit_block)]
    # A dedicated user books its own seats while claiming, in the body, to be
    # someone else; every booking must still belong to the token's user.
    jobs += [("spoof", "spoof", [seat], f"{run_id}-s{i}") for i, seat in enumerate(spoof_seats)]
    rng.shuffle(jobs)

    def fire(job):
        kind, user, seats, key = job
        extra = {"user_id": rng.choice(user_ids), "user": rng.choice(user_ids)} if kind == "spoof" else None
        return reserve("stampede", kind, user, seats, key, extra)

    stampede, elapsed = run_parallel(fire, jobs, args.concurrency, "stampede")
    all_results += stampede
    print("  outcome distribution:")
    print_distribution(stampede, elapsed)

    # ---------------------------------------------------------- idempotency
    print("\n[3/5] idempotency: same key, same body vs. different seats")
    key = f"{run_id}-k"
    first = reserve("idempotency", "first", "key", [key_seats[0]], key)
    all_results.append(first)
    jobs = [("same", [key_seats[0]]) for _ in range(10)] + [("different", [key_seats[1]]) for _ in range(10)]
    idem, _ = run_parallel(lambda j: reserve("idempotency", j[0], "key", j[1], key), jobs, 20, "idempotency")
    all_results += idem
    same = [r for r in idem if r.kind == "same"]
    diff = [r for r in idem if r.kind == "different"]
    print(f"    first request: {first.outcome}")
    print(f"    same body x10: {dict(collections.Counter(r.outcome for r in same))}")
    print(f"    different seats x10: {dict(collections.Counter(r.outcome for r in diff))}")

    # -------------------------------------------------------------- release
    print("\n[4/5] release: hot-seat winners cancel, freed seats are stormed again")
    winners = {}
    for r in stampede:
        if r.status == 201 and not r.replayed and r.seats[0] in hot and len(r.seats) == 1:
            winners[r.seats[0]] = r
    to_cancel = list(winners.values())
    cancel_jobs = []
    for r in to_cancel:
        rid = r.body["reservation_id"]
        owner = tokens[r.user]
        intruder = tokens[(r.user + 1) % len(tokens)]
        cancel_jobs += [("owner", rid, owner), ("owner", rid, owner), ("intruder", rid, intruder)]

    def cancel(job):
        kind, rid, tok = job
        st, resp, b, sec = client.request("POST", f"/reservations/{rid}/cancel", None, tok)
        return Result("release", f"cancel-{kind}", st, resp, b, sec, None, None, [], check=rid)

    cancels, _ = run_parallel(cancel, cancel_jobs, 50, "cancel")
    all_results += cancels
    cancelled_ids = {r.check for r in cancels if r.kind == "cancel-owner" and r.status == 200}
    print(f"    owner cancels: {dict(collections.Counter(r.status for r in cancels if r.kind == 'cancel-owner'))}"
          f"   other user's cancels: {dict(collections.Counter(r.status for r in cancels if r.kind == 'cancel-intruder'))}")

    freed = [r.seats[0] for r in to_cancel if r.body["reservation_id"] in cancelled_ids]
    restorm_jobs = [("restorm", rng.randrange(len(tokens)), [seat], f"{run_id}-r{seat}-{i}")
                    for seat in freed for i in range(100)]
    rng.shuffle(restorm_jobs)
    restorm, el = run_parallel(lambda j: reserve("release", *j), restorm_jobs, args.concurrency, "re-storm")
    all_results += restorm
    if restorm:
        print(f"  re-storm of {len(freed)} freed seats:")
        print_distribution(restorm, el)

    # ------------------------------------------------------------ reconcile
    print("\n[5/5] reconciliation")
    status, _, state, _ = client.request("GET", f"/shows/{show_id}", token=admin)
    counts, total = state["counts"], state["total_seats"]
    status_of = {s["seat"]: s["status"] for s in state["seats"]}
    print(f"  GET /shows/{show_id}: total {total}, available {counts['available']}, "
          f"held {counts['held']}, confirmed {counts['confirmed']}")

    # Every reservation the API confirmed to us, minus the ones cancelled.
    reservations = {}
    for r in all_results:
        if r.status == 201 and isinstance(r.body, dict) and "reservation_id" in r.body:
            reservations.setdefault(r.body["reservation_id"], r.body)
    active = {rid: b for rid, b in reservations.items() if rid not in cancelled_ids}
    holders = collections.defaultdict(set)
    per_user = collections.Counter()
    for rid, b in active.items():
        for seat in b["seats"]:
            holders[seat].add(rid)
        per_user[b["user_id"]] += len(b["seats"])

    bad = [r for r in all_results if not isinstance(r.status, int) or r.status >= 500]
    check("zero 5xx and transport errors across every phase", not bad,
          dict(collections.Counter(r.outcome for r in bad)) if bad else f"{len(all_results)} requests")
    check("invariant: available + held + confirmed == total_seats",
          sum(counts.values()) == total, f"{sum(counts.values())} vs {total}")
    double = {s: len(ids) for s, ids in holders.items() if len(ids) > 1}
    check("no seat confirmed to two reservations", not double, double or "")
    confirmed_seats = {s for s, st in status_of.items() if st == "confirmed"}
    check("seats confirmed by the API == seats in our live 201 responses",
          confirmed_seats == set(holders), f"{len(confirmed_seats)} vs {len(holders)}")
    for seat in hot:
        n = sum(1 for r in stampede if r.status == 201 and not r.replayed and seat in r.seats)
        check(f"hot seat {seat}: exactly one winner in the stampede", n == 1, f"{n} winners")
    for seat in freed:
        n = sum(1 for r in restorm if r.status == 201 and not r.replayed and seat in r.seats)
        check(f"freed seat {seat}: exactly one winner in the re-storm", n == 1, f"{n} winners")
    over = {u: n for u, n in per_user.items() if n > limit}
    check(f"no user holds more than {limit} seats", not over, over or f"max {max(per_user.values(), default=0)}")
    limit_wins = sum(1 for r in stampede if r.kind == "limit" and r.status == 201)
    check(f"one user firing 10 parallel requests got exactly {limit}",
          limit_wins == limit, f"{limit_wins} confirmed")
    by_key = collections.defaultdict(set)
    for r in all_results:
        if r.status == 201 and r.key:
            by_key[r.key].add(r.body["reservation_id"])
    multi = {k: v for k, v in by_key.items() if len(v) > 1}
    check("each idempotency key produced at most one reservation", not multi, multi or "")
    check("same key + same body: every resend replayed the original",
          first.status == 201 and all(r.status == 201 and r.replayed
                                      and r.body["reservation_id"] == first.body["reservation_id"] for r in same))
    check("same key + different seats: all 409 idempotency_key_reused",
          all(r.status == 409 and r.reason == "idempotency_key_reused" for r in diff))
    spoofed = [r for r in stampede if r.kind == "spoof"]
    check("a spoofed user_id in the body never changes who books",
          len(spoofed) == len(spoof_seats)
          and all(r.status == 201 and r.body["user_id"] == spoof_user_id for r in spoofed),
          f"{sum(r.status == 201 for r in spoofed)}/{len(spoofed)} booked as the token's user")
    intr = [r for r in cancels if r.kind == "cancel-intruder"]
    check("another user's cancel is refused (404)", all(r.status == 404 for r in intr), f"{len(intr)} attempts")

    status, _, text, _ = client.request("GET", "/metrics", root=True, text=True)
    if status == 200:
        gauges = {}
        for line in text.splitlines():
            for name in ("seats_available", "seats_held", "seats_confirmed", "seats_total"):
                if line.startswith(f'{name}{{show_id="{show_id}"}}'):
                    gauges[name] = float(line.rsplit(" ", 1)[1])
        expected = {"seats_available": counts["available"], "seats_held": counts["held"],
                    "seats_confirmed": counts["confirmed"], "seats_total": total}
        check("/metrics seat gauges match GET /shows", gauges == expected,
              f"{ {k.replace('seats_', ''): int(v) for k, v in gauges.items()} }")
    else:
        check("/metrics reachable", False, f"status {status}")

    print()
    if check.failed:
        print(f"FAILED: {len(check.failed)} check(s)")
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
