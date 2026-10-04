"""Auth endpoints: ``POST /api/v1/auth/register`` and ``POST /api/v1/auth/login``."""

from rest_framework import generics, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework_simplejwt.views import TokenObtainPairView

from .models import Tbl_Users
from .serializers import LoginSerializer, RegisterSerializer


class RegisterView(generics.CreateAPIView):
    """Create a user -> 201 ``{"message": "user created successfully"}``.

    Public endpoint (no JWT required).
    """

    queryset = Tbl_Users.objects.all()
    serializer_class = RegisterSerializer
    # Override the project default (IsAuthenticated) - this is part of signup.
    permission_classes = (AllowAny,)
    authentication_classes = ()

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(
            {'message': 'user created successfully'},
            status=status.HTTP_201_CREATED,
        )


class LoginView(TokenObtainPairView):
    """Exchange username + password for tokens -> 200 ``{"access", "refresh"}``.

    Public endpoint (no JWT required); a bad username/password yields 401.
    """

    serializer_class = LoginSerializer
    permission_classes = (AllowAny,)
    authentication_classes = ()

