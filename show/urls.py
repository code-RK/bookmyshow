"""URL routes for the show app, mounted at ``/api/v1/``.

``shows`` (no trailing slash) and ``shows/{id}`` live in this file instead of
being mounted under an ``api/v1/shows`` prefix: that prefix would need a
trailing slash for the detail route, and Django's APPEND_SLASH would then
redirect ``POST /api/v1/shows`` to ``/api/v1/shows/``, turning the POST into a
301 and dropping the request body (the same reason accounts/urls.py has no
trailing slashes).
"""

from django.urls import path

from .views import ShowCreateView, ShowDetailView, ReserveSeatView, ReservationDetailView, ReservationCancelView

urlpatterns = [
    path('shows', ShowCreateView.as_view(), name='show-create'),
    path('shows/<int:show_id>', ShowDetailView.as_view(), name='show-detail'),
    path('shows/<int:show_id>/reserve', ReserveSeatView.as_view(), name='reserve-seat'),
    path('reservations/<int:reservation_id>', ReservationDetailView.as_view(), name='reservation-detail'),
    path('reservations/<int:reservation_id>/cancel', ReservationCancelView.as_view(), name='reservation-cancel'),
]
