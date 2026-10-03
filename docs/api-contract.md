POST /api/v1/auth/register
POST /api/v1/auth/login

POST /api/v1/shows
GET  /api/v1/shows/{show_id}

POST /api/v1/shows/{show_id}/reserve

GET  /api/v1/reservations/{reservation_id}
POST /api/v1/reservations/{reservation_id}/cancel

GET /health/live
GET /health/ready
GET /metrics

<!-- Request / Response structure -->
POST /api/v1/auth/register
Content-Type: application/json
{
    "username": "test",
    "password": "pass@123"
}
Response:
{
  "message": "user created successfully"
}

POST /api/v1/auth/login
Content-Type: application/json
{
    "username": "test",
    "password": "pass@123"
}
Response:
{
  "access": "amshb...",
  "refresh": "asdkn..."
}

POST /api/v1/shows
Authorization: Bearer <JWT>   (admin role only -> 403 for a non-admin)
Content-Type: application/json
{
    "name": "friday-night",
    "seats": ["A1","A2","A3","..."],
    "price_paise": 25000
}
Response: 201 (same shape as GET /api/v1/shows/{show_id})

GET /api/v1/shows/{show_id}
Authorization: Bearer <JWT>   (any authenticated user)
Response: 200
{
    "id": 1,
    "name": "friday-night",
    "price_paise": 25000,
    "per_user_limit": 4,
    "total_seats": 3,
    "counts": {"available": 1, "held": 0, "confirmed": 2},
    "seats": [
        {"seat": "A1", "status": "confirmed"},
        {"seat": "A2", "status": "confirmed"},
        {"seat": "A3", "status": "available"}
    ]
}
available + held + confirmed == total_seats in every response.
held is always 0: a reservation confirms its seats immediately (no hold phase).

POST /api/v1/shows/{show_id}/reserve
Authorization: Bearer <JWT>
Idempotency-Key: <key>        (or "idempotency_key" in the body; if both are sent they must match)
Content-Type: application/json
{
    "seats": ["A1", "A2"],
    "idempotency_key": "..."
}
Response: 201
{
    "reservation_id": 1,
    "show_id": 1,
    "user_id": 5,
    "seats": ["A1", "A2"],
    "amount_paise": 50000,
    "status": "confirmed"
}
- user_id always comes from the token; any user field in the body is ignored.
- All-or-nothing: if any requested seat is taken, nothing is booked (409).
- Same key + same request -> the original response again (nothing new is booked).
- Same key + different seats/show (or another user's key) -> 409.
- 400 malformed body or missing key, 404 unknown show,
  409 seat taken / per_user_limit exceeded / key reused.

GET /api/v1/reservations/{reservation_id}
Authorization: Bearer <JWT>   (owner only; anyone else gets 404)
Response: 200
{
    "reservation_id": 1,
    "show_id": 1,
    "user_id": 5,
    "seats": ["A1", "A2"],
    "amount_paise": 50000,
    "status": "confirmed"          ("cancelled" after a cancel; seats are kept)
}

POST /api/v1/reservations/{reservation_id}/cancel
Authorization: Bearer <JWT>   (owner only; anyone else gets 404)
Response: 200 (also 200 when already cancelled)
{
    "reservation_id": 1,
    "show_id": 1,
    "seats": ["A1", "A2"],
    "status": "cancelled"
}
The seats become available again and can be booked by anyone.

GET /health/live
Response:
{
    "status": "ok"
}

GET /health/ready
Response:
{
    "status": "ok",
    "checks": {
        "database": "ok",
        "cache": "ok"
    }
}

GET /metrics
Response:
# Prometheus text exposition format (OpenMetrics)
# HELP http_requests_total Total number of HTTP requests
# TYPE http_requests_total counter
http_requests_total{method="POST",path="/api/v1/shows/1/reserve",status="200"} 42
# HELP reservation_conflicts_total Total number of seat reservation conflicts
# TYPE reservation_conflicts_total counter
reservation_conflicts_total 3

<!-- status codes -->
201 → new reservation
200 → idempotent replay
400 → invalid request
401 → unauthenticated
403 → unauthorized
404 → show/reservation not found
409 → business conflict
500 → unexpected server error