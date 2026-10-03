import hashlib
import json
import random
import re
import time

from django.db import IntegrityError, OperationalError, transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsAdminRole
from bookmyshow.metrics import (
    RESERVATION_ATTEMPTS,
    RESERVATION_CANCELLATIONS,
    RESERVATION_CONFLICTS,
    RESERVATION_LATENCY,
    RESERVATION_SUCCESS,
)

from .models import Tbl_Seat, Tbl_Show, Tbl_Reservation, Tbl_Reservation_Seat, Tbl_Idempotency_Key


# MySQL errors after which re-running the whole transaction is safe and
# usually succeeds: 1213 = deadlock (MySQL rolled the transaction back),
# 1205 = lock wait timeout (innodb_lock_wait_timeout, see settings.py).
RETRYABLE_MYSQL_ERRORS = {1213, 1205}
MAX_TRANSACTION_ATTEMPTS = 4


class ContentionError(Exception):
    """Every attempt hit a deadlock or lock wait timeout."""


def run_atomic_with_retry(fn):
    """Run ``fn()`` in a transaction, re-running it on deadlock or lock timeout.

    Each attempt is a fresh transaction, so a retry never sees half of a
    rolled-back attempt. Raises ContentionError once every attempt has
    failed, so the caller can answer with a clean 409 instead of a 500.
    """
    for attempt in range(1, MAX_TRANSACTION_ATTEMPTS + 1):
        try:
            with transaction.atomic():
                return fn()
        except OperationalError as exc:
            if not exc.args or exc.args[0] not in RETRYABLE_MYSQL_ERRORS:
                raise
            if attempt == MAX_TRANSACTION_ATTEMPTS:
                raise ContentionError from exc
            # Random backoff so the transactions that collided do not
            # collide again on the next attempt.
            time.sleep(random.uniform(0, 0.02 * 2 ** attempt))


def show_payload(show):
    """The show with every seat's status and the per-status counts.

    The seats and the counts come from one SELECT, so they always agree and
    ``available + held + confirmed == total_seats`` holds in every response.
    ``held`` is always 0: a reservation confirms its seats immediately.
    """
    # Tbl_Seat.Meta.ordering is ('id',), so seats come back in the order they
    # were created.
    seats = [
        {"seat": seat_number, "status": seat_status}
        for seat_number, seat_status in show.seats.values_list("seat_number", "status")
    ]
    counts = {"available": 0, "held": 0, "confirmed": 0}
    for seat in seats:
        counts[seat["status"]] += 1

    return {
        "id": show.id,
        "name": show.name,
        "price_paise": show.price_paise,
        "per_user_limit": show.per_user_limit,
        "total_seats": len(seats),
        "counts": counts,
        "seats": seats,
    }


def reservation_seat_numbers(reservation):
    """Seat numbers a reservation covers, kept even after it is cancelled."""
    return list(
        Tbl_Reservation_Seat.objects
        .filter(reservation=reservation)
        .order_by("seat_id")
        .values_list("seat__seat_number", flat=True)
    )


class ShowCreateView(APIView):
    """``POST /api/v1/shows`` -> 201 with the created show.

    Admin only: a missing/invalid bearer token yields 401, a valid token for a
    non-admin yields 403, and a valid admin token yields 201.
    """

    permission_classes = (IsAuthenticated, IsAdminRole)

    def post(self, request):
        if not isinstance(request.data, dict):
            return Response(
                {"message": "Request body must be a JSON object"},
                status=status.HTTP_400_BAD_REQUEST
            )

        name = request.data.get("name")
        if not isinstance(name, str) or not name.strip():
            return Response(
                {"message": "name is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        name = name.strip()
        if len(name) > 200:
            return Response(
                {"message": "name must be at most 200 characters"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Accepts what DRF's IntegerField accepted: an int, or a whole number
        # as a string or float (5, "5", 5.0, "5.0"). bool is excluded because
        # it is a subclass of int.
        price_paise = request.data.get("price_paise")
        try:
            if isinstance(price_paise, bool):
                raise ValueError
            price_paise = int(re.sub(r"\.0*\s*$", "", str(price_paise).strip()))
        except ValueError:
            return Response(
                {"message": "price_paise must be a whole number"},
                status=status.HTTP_400_BAD_REQUEST
            )
        if price_paise < 1:
            return Response(
                {"message": "price_paise must be at least 1"},
                status=status.HTTP_400_BAD_REQUEST
            )

        seats = request.data.get("seats")
        if not isinstance(seats, list) or not seats:
            return Response(
                {"message": "seats must be a non-empty list of seat numbers"},
                status=status.HTTP_400_BAD_REQUEST
            )
        if any(
            not isinstance(seat, str) or not seat.strip() or len(seat.strip()) > 20
            for seat in seats
        ):
            return Response(
                {"message": "Each seat must be a non-blank string of at most 20 characters"},
                status=status.HTTP_400_BAD_REQUEST
            )
        seats = [seat.strip() for seat in seats]

        # Compared case-insensitively because MySQL's default collation is
        # case-insensitive and would reject the duplicate at insert time
        # with an IntegrityError (500) instead of a 400.
        seen = set()
        duplicates = set()
        for seat in seats:
            if seat.lower() in seen:
                duplicates.add(seat)
            seen.add(seat.lower())
        if duplicates:
            return Response(
                {"message": f"Duplicate seat number(s): {', '.join(sorted(duplicates))}"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # The show and its seats must exist together or not at all.
        with transaction.atomic():
            show = Tbl_Show.objects.create(
                name=name,
                price_paise=price_paise,
            )
            Tbl_Seat.objects.bulk_create([
                Tbl_Seat(show=show, seat_number=number)
                for number in seats
            ])

        # bulk_create does not set primary keys on MySQL, so the seats are read
        # back rather than taken from the objects above.
        return Response(show_payload(show), status=status.HTTP_201_CREATED)


class ShowDetailView(APIView):
    """``GET /api/v1/shows/{show_id}`` -> 200, or 404 when the show is missing.

    Returns every seat's status plus available / held / confirmed counts.
    Readable by any authenticated user (admin or customer).
    """

    # Same as the project default, spelled out because it is the endpoint's
    # contract: an anonymous caller must get 401.
    permission_classes = (IsAuthenticated,)

    def get(self, request, show_id):
        try:
            show = Tbl_Show.objects.get(id=show_id)
        except Tbl_Show.DoesNotExist:
            return Response(
                {"message": "Show does not exist"},
                status=status.HTTP_404_NOT_FOUND
            )

        return Response(show_payload(show), status=status.HTTP_200_OK)

class ReserveSeatView(APIView):
    """``POST /api/v1/shows/{show_id}/reserve`` -> 201 with the reservation.

    Open to any authenticated user (admin or customer). Requires an
    idempotency key, as the ``Idempotency-Key`` header or an
    ``idempotency_key`` body field: replaying a key with the same request
    returns the original response instead of booking again.

    All-or-nothing: if any requested seat is taken, nothing is booked.

    400 invalid request, 404 show missing, 409 seat/limit/key conflict.
    """

    permission_classes = (IsAuthenticated,)

    @RESERVATION_LATENCY.time()
    def post(self, request, show_id):
        RESERVATION_ATTEMPTS.inc()

        if not isinstance(request.data, dict):
            return Response(
                {"message": "Request body must be a JSON object"},
                status=status.HTTP_400_BAD_REQUEST
            )

        header_key = request.headers.get("Idempotency-Key", "").strip()
        body_key = request.data.get("idempotency_key", "")
        if not isinstance(body_key, str):
            return Response(
                {"message": "idempotency_key must be a string"},
                status=status.HTTP_400_BAD_REQUEST
            )
        body_key = body_key.strip()
        if header_key and body_key and header_key != body_key:
            return Response(
                {"message": "Idempotency-Key header and idempotency_key body field differ"},
                status=status.HTTP_400_BAD_REQUEST
            )
        idempotency_key = header_key or body_key
        if not idempotency_key:
            return Response(
                {"message": "An idempotency key is required (Idempotency-Key header or idempotency_key field)"},
                status=status.HTTP_400_BAD_REQUEST
            )
        if len(idempotency_key) > 100:
            return Response(
                {"message": "The idempotency key must be at most 100 characters"},
                status=status.HTTP_400_BAD_REQUEST
            )

        seats = request.data.get("seats")
        if not isinstance(seats, list) or not seats:
            return Response(
                {"message": "seats must be a non-empty list of seat numbers"},
                status=status.HTTP_400_BAD_REQUEST
            )
        if any(
            not isinstance(seat, str) or not seat.strip() or len(seat.strip()) > 20
            for seat in seats
        ):
            return Response(
                {"message": "Each seat must be a non-blank string of at most 20 characters"},
                status=status.HTTP_400_BAD_REQUEST
            )
        seats = [seat.strip() for seat in seats]

        # Compared case-insensitively to match MySQL's default collation.
        if len({seat.lower() for seat in seats}) != len(seats):
            return Response(
                {"message": "Duplicate seats are not allowed"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            show = Tbl_Show.objects.get(id=show_id)
        except Tbl_Show.DoesNotExist:
            return Response(
                {"message": "Show does not exist"},
                status=status.HTTP_404_NOT_FOUND
            )

        # A clean decline like the per-user check below, not a 400: the
        # request is well-formed, it just asks for more than the limit allows.
        if len(seats) > show.per_user_limit:
            RESERVATION_CONFLICTS.labels("user_limit_exceeded").inc()
            return Response(
                {"message": f"You can book at most {show.per_user_limit} seats for this show"},
                status=status.HTTP_409_CONFLICT
            )

        # Deterministic hash of the request, to detect a key reused for a
        # different request.
        payload = {"show_id": show.id, "seats": sorted(seats)}
        request_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest()

        # Fast decline, without locks or a transaction: in a hot-seat storm
        # almost every request is for a seat that is already gone, and this
        # keeps them from queueing on the seat's row lock. It only ever
        # declines; whether a seat is actually free is still decided under
        # the lock below.
        #
        # The key is read *after* the seats: if a seat shows as taken by this
        # same request's earlier attempt, that booking (and its key, written
        # in the same transaction) has committed, so the key is visible and
        # the retry goes on to be replayed rather than declined.
        taken = list(
            Tbl_Seat.objects
            .filter(show=show, seat_number__in=seats)
            .exclude(status=Tbl_Seat.Status.AVAILABLE)
            .values_list("seat_number", flat=True)
        )
        if taken and not Tbl_Idempotency_Key.objects.filter(key=idempotency_key).exists():
            RESERVATION_CONFLICTS.labels("seats_unavailable").inc()
            return Response(
                {
                    "message": "Some seats are unavailable",
                    "unavailable_seats": taken,
                },
                status=status.HTTP_409_CONFLICT
            )

        try:
            response, booked = run_atomic_with_retry(
                lambda: self._reserve_locked(request, show, seats, idempotency_key, request_hash)
            )
        except ContentionError:
            RESERVATION_CONFLICTS.labels("contention").inc()
            return Response(
                {"message": "The seats are under heavy contention, please retry"},
                status=status.HTTP_409_CONFLICT
            )
        except IntegrityError:
            # Another user inserted the same idempotency key at the same
            # moment (requests from one user are serialized by the user-row
            # lock, so this is always a different user's request).
            if not Tbl_Idempotency_Key.objects.filter(key=idempotency_key).exists():
                raise
            RESERVATION_CONFLICTS.labels("idempotency_key_reused").inc()
            return Response(
                {"message": "This idempotency key was already used for a different request"},
                status=status.HTTP_409_CONFLICT
            )

        # Counted after the transaction, i.e. once the booking is committed.
        if booked:
            RESERVATION_SUCCESS.inc()
        return response

    @staticmethod
    def _reserve_locked(request, show, seats, idempotency_key, request_hash):
        """The booking itself, run inside one transaction.

        Returns ``(response, booked)``. Every early return happens before any
        write, so returning from inside the transaction commits nothing.
        Locks are always taken in the same order - the user's row, then the
        seats sorted by seat number - so two requests cannot deadlock by
        locking the same rows in opposite orders.
        """
        # Lock this user's row so two concurrent requests from the same user
        # cannot bypass the per-user limit or reuse a key.
        user = type(request.user).objects.select_for_update().get(pk=request.user.pk)

        # The key is unique across all users, so look it up by key alone.
        existing_key = Tbl_Idempotency_Key.objects.filter(key=idempotency_key).first()
        if existing_key:
            if (
                existing_key.user_id != user.id
                or existing_key.request_hash != request_hash
            ):
                RESERVATION_CONFLICTS.labels("idempotency_key_reused").inc()
                return Response(
                    {"message": "This idempotency key was already used for a different request"},
                    status=status.HTTP_409_CONFLICT
                ), False
            RESERVATION_CONFLICTS.labels("idempotent_replay").inc()
            return Response(
                existing_key.response_body,
                status=existing_key.status_code
            ), False

        existing_seat_count = Tbl_Seat.objects.filter(
            show=show,
            current_reservation__user=user,
            current_reservation__status=Tbl_Reservation.Status.CONFIRMED,
        ).count()

        if existing_seat_count + len(seats) > show.per_user_limit:
            RESERVATION_CONFLICTS.labels("user_limit_exceeded").inc()
            return Response(
                {
                    "message": (
                        f"You can book at most {show.per_user_limit} seats "
                        f"for this show. You already have {existing_seat_count}."
                    )
                },
                status=status.HTTP_409_CONFLICT
            ), False

        # Lock the requested seats, in seat-number order (see the docstring).
        seat_list = list(
            Tbl_Seat.objects
            .select_for_update()
            .filter(show=show, seat_number__in=seats)
            .order_by("seat_number")
        )
        if len(seat_list) != len(seats):
            return Response(
                {"message": "One or more selected seats do not exist"},
                status=status.HTTP_400_BAD_REQUEST
            ), False

        unavailable = [
            seat.seat_number
            for seat in seat_list
            if seat.status != Tbl_Seat.Status.AVAILABLE
        ]
        if unavailable:
            RESERVATION_CONFLICTS.labels("seats_unavailable").inc()
            return Response(
                {
                    "message": "Some seats are unavailable",
                    "unavailable_seats": unavailable,
                },
                status=status.HTTP_409_CONFLICT
            ), False

        amount_paise = show.price_paise * len(seats)

        reservation = Tbl_Reservation.objects.create(
            show=show,
            user=user,
            status=Tbl_Reservation.Status.CONFIRMED,
            amount_paise=amount_paise
        )

        for seat in seat_list:
            seat.status = Tbl_Seat.Status.CONFIRMED
            seat.current_reservation = reservation

        Tbl_Seat.objects.bulk_update(
            seat_list,
            ["status", "current_reservation"]
        )

        # Permanent record of which seats this reservation covered;
        # current_reservation is cleared again when it is cancelled.
        Tbl_Reservation_Seat.objects.bulk_create([
            Tbl_Reservation_Seat(reservation=reservation, seat=seat)
            for seat in seat_list
        ])

        result = {
            "reservation_id": reservation.id,
            "show_id": show.id,
            "user_id": user.id,
            "seats": seats,
            "amount_paise": amount_paise,
            "status": Tbl_Reservation.Status.CONFIRMED,
        }

        # Stored only on success, so a failed attempt can be retried with the
        # same key.
        Tbl_Idempotency_Key.objects.create(
            key=idempotency_key,
            user=user,
            request_hash=request_hash,
            reservation=reservation,
            response_body=result,
            status_code=status.HTTP_201_CREATED,
        )

        return Response(result, status=status.HTTP_201_CREATED), True

class ReservationDetailView(APIView):
    """``GET /api/v1/reservations/{reservation_id}`` -> 200, or 404.

    Only the owner can see a reservation; anyone else gets 404, so ids of
    other users' reservations are not revealed.
    """

    permission_classes = (IsAuthenticated,)

    def get(self, request, reservation_id):
        try:
            reservation = (
                Tbl_Reservation.objects
                .select_related("show", "user")
                .get(id=reservation_id, user=request.user)
            )
        except Tbl_Reservation.DoesNotExist:
            return Response(
                {"message": "Invalid reservation id"},
                status=status.HTTP_404_NOT_FOUND
            )

        result = {
            "reservation_id": reservation.id,
            "show_id": reservation.show_id,
            "user_id": reservation.user_id,
            "seats": reservation_seat_numbers(reservation),
            "amount_paise": reservation.amount_paise,
            "status": reservation.status,
        }

        return Response(result, status=status.HTTP_200_OK)

class ReservationCancelView(APIView):
    """``POST /api/v1/reservations/{reservation_id}/cancel`` -> 200, or 404.

    Only the owner can cancel (anyone else gets 404). Cancelling twice
    returns 200 with the cancelled reservation again.
    """

    permission_classes = (IsAuthenticated,)

    def post(self, request, reservation_id):
        try:
            return run_atomic_with_retry(
                lambda: self._cancel_locked(request, reservation_id)
            )
        except ContentionError:
            return Response(
                {"message": "The reservation is under heavy contention, please retry"},
                status=status.HTTP_409_CONFLICT
            )

    @staticmethod
    def _cancel_locked(request, reservation_id):
        """The cancellation itself, run inside one transaction."""
        try:
            reservation = (
                Tbl_Reservation.objects
                .select_for_update()
                .get(id=reservation_id, user=request.user)
            )
        except Tbl_Reservation.DoesNotExist:
            return Response(
                {"message": "Invalid reservation id"},
                status=status.HTTP_404_NOT_FOUND
            )

        if reservation.status == Tbl_Reservation.Status.CANCELLED:
            return Response(
                {
                    "reservation_id": reservation.id,
                    "status": Tbl_Reservation.Status.CANCELLED,
                    "message": "Reservation is already cancelled"
                },
                status=status.HTTP_200_OK
            )

        if reservation.status != Tbl_Reservation.Status.CONFIRMED:
            return Response(
                {"message": "Only confirmed reservations can be cancelled"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Lock the seats this reservation still holds, in the same seat-number
        # order the reserve view uses, so the two cannot deadlock. Filtering
        # on current_reservation means a seat that has since moved to another
        # reservation is never released by this one.
        seat_list = list(
            Tbl_Seat.objects
            .select_for_update()
            .filter(current_reservation=reservation)
            .order_by("seat_number")
        )

        # Release seats.
        for seat in seat_list:
            seat.status = Tbl_Seat.Status.AVAILABLE
            seat.current_reservation = None

        Tbl_Seat.objects.bulk_update(
            seat_list,
            ["status", "current_reservation"]
        )

        # Cancel reservation.
        reservation.status = Tbl_Reservation.Status.CANCELLED
        reservation.cancelled_at = timezone.now()
        reservation.save(update_fields=["status", "cancelled_at"])
        # Dropped automatically if this attempt rolls back and is retried.
        transaction.on_commit(RESERVATION_CANCELLATIONS.inc)

        result = {
            "reservation_id": reservation.id,
            "show_id": reservation.show_id,
            "seats": reservation_seat_numbers(reservation),
            "status": Tbl_Reservation.Status.CANCELLED
        }

        return Response(result, status=status.HTTP_200_OK)
