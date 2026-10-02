User
 ├── id
 ├── email
 ├── password
 └── role

Show
 ├── id
 ├── name
 ├── price_paise
 └── per_user_limit

Seat
 ├── id
 ├── show_id
 ├── seat_number
 ├── status
 └── current_reservation_id

Reservation
 ├── id
 ├── show_id
 ├── user_id
 ├── amount_paise
 ├── status
 ├── created_at
 └── cancelled_at

ReservationSeat
 ├── reservation_id
 └── seat_id

IdempotencyRecord
 ├── user_id
 ├── show_id
 ├── idempotency_key
 ├── request_hash
 └── reservation_id