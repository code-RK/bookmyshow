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
Authorization: Bearer <JWT>
Content-Type: application/json
{
    "name": "friday-night",
    "seats": ["A1","A2","A3","..."],
    "price_paise": 25000
}
Response:
{
    "id": 1,
    "seats": [
        {
            "seat": "A1",
            "status": "available"
        }
        ...
    ]
}

GET /api/v1/shows/{show_id}
Authorization: Bearer <JWT>
Response:
{
    "id": 1,
    "name": "friday-night",
    "price_paise": 25000,
    "seats": [
        {
            "seat": "A1",
            "status": "available"
        }
        ...
    ]
}

POST /api/v1/shows/{show_id}/reserve
Authorization: Bearer <JWT>
Content-Type: application/json
{
    "seats": ["A1", "A2"]
}
Response:
{
    "id": "rsv_1a2b3c...",
    "show_id": 1,
    "status": "held",
    "seats": ["A1", "A2"],
    "expires_at": "2026-10-02T12:05:00Z"
}

GET /api/v1/reservations/{reservation_id}
Authorization: Bearer <JWT>
Response:
{
    "id": "rsv_1a2b3c...",
    "show_id": 1,
    "status": "held",
    "seats": ["A1", "A2"],
    "expires_at": "2026-10-02T12:05:00Z"
}

POST /api/v1/reservations/{reservation_id}/cancel
Authorization: Bearer <JWT>
Response:
{
    "message": "reservation cancelled successfully"
}

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