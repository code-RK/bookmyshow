"""URL routes for the accounts app, mounted at ``/api/v1/auth/``.

The paths intentionally have no trailing slash so that they match
``docs/api-contract.md`` exactly (``POST /api/v1/auth/register``). Django's
APPEND_SLASH redirect would otherwise turn a POST into a 301 and lose the body.
"""

from django.urls import path

from .views import LoginView, RegisterView

urlpatterns = [
    path('register', RegisterView.as_view(), name='auth-register'),
    path('login', LoginView.as_view(), name='auth-login'),
]
