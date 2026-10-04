# Write-up: seat reservation under load

Django + Django REST Framework on gunicorn, backed by a single MySQL 8 primary.
Live at https://bookmyshow-production-3db6.up.railway.app. Burst results from
that deployment are in [`results/`](results/).

## 1. The atomic decision

**Mechanism: pessimistic row locks inside one MySQL transaction.** A
reservation is one `transaction.atomic()` block that takes locks in a fixed
order:

1. `SELECT ... FOR UPDATE` on the **user's own row** (`tbl_users`).
2. `SELECT ... FOR UPDATE` on the **requested seat rows**, `ORDER BY
   seat_number`.
3. With both held, it checks every seat's `status`. Only if all are
   `available` does it insert the reservation, set the seats to `confirmed`
   with `current_reservation` pointing at it, and commit.

**Why this cannot double-sell.** A seat is one row (unique on `(show,
seat_number)`), and ownership lives on that row: `status` plus a single
`current_reservation` column. A seat therefore cannot belong to two
reservations at once by construction. The read ("is A12 free?") and the write
("take it") happen while holding A12's row lock, so no other transaction can
read A12 in between. When 500 requests race for A12, they queue on that lock.
The first commits; each later one gets the lock, re-reads the row (READ
COMMITTED, so a locking read sees the committed `confirmed`) and returns a
clean `409 {"reason": "seats_unavailable"}`. There is no read-then-write
window.

**Per-user limit.** Locking the user's row first serializes all of one user's
reservation requests. The "seats you already hold + seats you ask for ≤
per_user_limit" check runs inside that serialized section, so 10 parallel
requests from one user on a limit-4 show end with exactly 4 seats. A request
asking for more than the limit by itself is declined before any lock.

**Multi-seat requests: all-or-nothing, and no deadlocks.** If any requested
seat is taken, nothing is booked (`409` listing the unavailable seats). Every
code path locks in the same global order: user row, then seats sorted by seat
number. Cancel locks the reservation row, then its seats in the same seat
order. Two transactions can therefore never hold each other's next lock. As a
safety net, a deadlock (MySQL 1213) or lock-wait timeout (1205, set to 5 s) is
retried as a fresh transaction, up to 4 attempts with random backoff. After
that the client gets `409 {"reason": "contention"}`, never a 500. In every
burst run, no request ever reached that point.

**Fast decline for hot seats.** Before opening a transaction, a plain
lock-free read checks whether any requested seat is already confirmed. If one
is, the request is declined immediately. This is only a shortcut: it can only
decline, and a seat is only ever *granted* under the lock. Without it, every
loser of a hot-seat storm would queue on that seat's row lock. With it, a
storm of 2,000 requests on one seat caused 18 lock waits in total. The
shortcut reads the seats *before* the idempotency key, so a retry of a request
that already succeeded is replayed rather than declined. (The booking and its
key commit together, so if the seat shows as taken by that booking, the key is
visible too.)

**Alternative considered.** A conditional update (`UPDATE seat SET
status='confirmed' ... WHERE status='available'`, then checking the row count)
is equally race-free. I used row locks because the same transaction must also
enforce the per-user limit and the idempotency record, and report *which*
seats were unavailable.

## 2. Idempotency

- **Where it is stored:** `tbl_idempotency_key`. Each row holds the key
  (unique), the user, a SHA-256 of the request (show id + sorted seat list),
  the reservation, and the exact response body and status code that were
  returned.
- **How exactly-once is enforced:** the key row is inserted *in the same
  transaction* as the reservation and seat updates. They commit together or
  not at all: there is never a booking without its key, or a key without its
  booking. The key comes from the `Idempotency-Key` header or an
  `idempotency_key` body field (if both are sent, they must match).
- **Retries:** the key is looked up after the user-row lock is taken. A
  concurrent retry from the same user therefore waits for the original to
  commit, then finds the key and gets the original `201` response back, marked
  with `Idempotent-Replayed: true`. Nothing new is booked.
- **Same key, different body:** the request hash differs, so the answer is
  `409 {"reason": "idempotency_key_reused"}`. Keys are unique across all
  users. If another user's request inserts the same key at the same moment,
  the unique index rejects the second insert and that request gets the same
  409.
- **Failed attempts are not stored.** A request that was declined (seat taken,
  over the limit) leaves no key behind, so the same key can be retried later
  and is evaluated afresh. This is a deliberate choice: a decline is not a
  state change worth replaying.

## 3. Holds and expiry

I chose the **explicit-cancel** model. A successful reservation confirms its
seats immediately; there is no separate held phase, so `held` is always 0 and
`available + held + confirmed == total_seats` holds trivially per row.
`POST /reservations/{id}/cancel`:

- works only for the owner. Anyone else gets `404`, which also does not reveal
  that the reservation exists;
- locks the reservation, then releases only the seats whose
  `current_reservation` is still *this* reservation. A cancel therefore cannot
  resurrect a seat that has since been confirmed to someone else;
- is idempotent: cancelling twice returns `200` with the same result.

A released seat is immediately bookable. In the live burst, every seat
released by a cancel was stormed again by 100 requests and had exactly one
winner each time.

## 4. Consistency vs. availability under a partition

The service is **CP**: MySQL, a single primary, is the only source of truth,
and no decision is made without it.

- If the app cannot reach the database, `/health/ready` fails closed (`503`,
  and the cause is logged), so the platform stops routing traffic to that
  instance. Reservations fail rather than guess. Today that failure surfaces
  as a `500` (see "What I'd do next").
- If the connection breaks *during* a commit, the client cannot know whether
  the booking happened. That is what the idempotency key is for: retrying with
  the same key once the database is back returns the original result if it
  committed, or books exactly once if it did not.
- Scaling out app instances does not change correctness. Every instance
  defers to the same row locks, so more replicas means more throughput, never
  a second opinion about a seat.

## 5. Observability

- **Metrics** at `/metrics` (Prometheus): `reservation_success_total`,
  `reservation_conflicts_total{reason}` (`seats_unavailable`,
  `user_limit_exceeded`, `idempotency_key_reused`, `idempotent_replay`,
  `contention`), `reservation_cancellations_total`,
  `reservation_latency_seconds`, `http_requests_total` and duration by route
  and status, plus per-show seat gauges (`seats_available`, `seats_held`,
  `seats_confirmed`, `seats_total`). The seat gauges are counted from the
  database at scrape time, so they always match `GET /shows/{id}`. Counters
  from all gunicorn workers are summed (multi-process mode). After the live
  20k run, every counter matched the burst's outcome counts exactly (compare
  [`results/burst-live-20k.metrics.txt`](results/burst-live-20k.metrics.txt)
  with the run output).
- **Logs:** one JSON line per request on stdout, with a request id (the
  caller's `X-Request-ID`, or a generated one returned in that header). Each
  line carries the route, status, duration, user, outcome, decline reason and
  reservation id. Errors carry their traceback under the same id. Caveat: at
  peak burst rate, one line per request exceeds Railway's ~500 lines/s limit,
  and the platform drops some routine decline lines. Exact counts come from
  the metrics; logs are for tracing individual requests.
- **Dashboard:** a provisioned Grafana dashboard (`docker compose up`) covers
  bookings, declines by reason, latency, seat inventory and HTTP status. It
  can be pointed at the live service.

**What I would get paged for at 2am:**

| Alert | Why it matters |
| --- | --- |
| any `5xx` on reserve/cancel for more than a minute | declines should always be 4xx; a 5xx means a bug or the database |
| `/health/ready` failing | the database is unreachable, so no bookings at all |
| `reservation_conflicts_total{reason="contention"}` increasing | locks are timing out even after retries: overload or a stuck transaction |
| reserve p99 above ~2 s for 5 minutes | queueing on locks or the database; users time out and retry |
| successes drop to ~0 while `seats_available` > 0 during an on-sale | people are being turned away from seats that exist |

## 6. Testing and results

`./burst.sh <BASE_URL>` (stdlib Python, [README](README.md#burst-test-on-sale-stampede))
creates a fresh show and fires the stampede, all shuffled together:

- a hot-seat storm;
- general traffic;
- 10% same-key retries;
- one user sending 10 parallel requests;
- spoofed `user_id`s in the body.

Then it runs a key-reuse test, cancels the hot-seat winners and storms the
freed seats again. Finally it checks 23 invariants against `GET /shows/{id}`
and `/metrics`.

Against the live deployment, 20,000 requests with 200 in flight
([`results/burst-live-20k.txt`](results/burst-live-20k.txt)):

- all checks passed, with **zero 5xx and zero transport errors** over 20,533
  requests, at 361 req/s;
- outcomes: 450 confirmed, 73 replays, 19,424 `seats_unavailable`, 50
  `user_limit_exceeded`;
- one winner per hot seat, and again per freed seat;
- 493 confirmed + 7 available = 500.

Server-side, all 20,518 reservations completed within 2.5 s, and 99.7% within
1 s. The few slow outliers seen by the client (up to ~24 s) waited in
Railway's edge proxy or in connection setup, before reaching the app.

## 7. AI usage

<!-- TODO (Rahul): write this section yourself, in your own words - the spec
asks for an honest account of what you directed vs. what you decided, and the
interviewers will ask you to extend this code live. Some prompts:

- Which tool(s) you used (e.g. Claude Code in VS Code) and for which parts.
- What you wrote or designed yourself before using AI (e.g. the data model,
  docs/api-contract.md, the first views and serializers, the JWT auth).
- Decisions you made or overrode, for example: moving logic out of
  serializers into views; keeping all routes under /api/v1; configuring the
  database through separate DB_* variables instead of DATABASE_URL; keeping
  one log line per request instead of sampling declines.
- What AI produced that you reviewed and accepted (e.g. the locking/retry
  structure, idempotency flow, burst script, metrics/logging, deployment
  setup) - and what you verified yourself (the burst runs, the live deploy,
  reading the code).
- Anything AI got wrong that you or testing caught.
-->

## 8. What I would do next

- **Database outage → clean 503.** Map database connection errors on the
  reserve and cancel endpoints to `503 Retry-After` instead of a 500, so
  clients retry with their idempotency key.
- **Time-boxed holds.** Add a `held` state with an expiry, a "confirm"
  step, and a sweeper that releases expired holds using the same
  `current_reservation`-guarded update as cancel.
- **Idempotency key hygiene.** Scope uniqueness to `(user, key)`, so one user
  cannot squat on another's key, and expire keys after a TTL.
- **Throughput.** Switch to `mysqlclient` (C driver): PyMySQL's pure-Python
  protocol is about 20% of request CPU. Also trim database round trips per
  booking, and add a waiting room or admission control for extreme on-sales.
- **Logs.** Sample routine 409 lines, with periodic counts of the skipped
  ones, to stay under platform log-rate limits.
- **Tests.** Add Django tests for the reserve, cancel and idempotency rules,
  so the invariants are checked in CI and not only by the burst script.
