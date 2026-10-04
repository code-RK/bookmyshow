"""Serializers for the accounts app: registration and JWT login."""

from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .models import Tbl_Users


class RegisterSerializer(serializers.ModelSerializer):
    """Validate and create a new user.

    Request body (docs/api-contract.md)::

        {"username": "test", "password": "pass@123"}
    """

    # write_only so the hash is never echoed back in a response, and
    # validate_password so Django's AUTH_PASSWORD_VALIDATORS are enforced.
    password = serializers.CharField(
        write_only=True,
        style={'input_type': 'password'},
        validators=[validate_password],
    )

    class Meta:
        model = Tbl_Users
        # ModelSerializer adds a UniqueValidator for username automatically, so
        # a duplicate registration is rejected with HTTP 400.
        #
        # `role` is deliberately absent: it would let a client register itself
        # as an admin. New users get the model default (customer).
        fields = ('id', 'username', 'email', 'password')

    def create(self, validated_data):
        # create_user() hashes the password; never store it in plain text.
        return Tbl_Users.objects.create_user(**validated_data)


class LoginSerializer(TokenObtainPairSerializer):
    """SimpleJWT's default login serializer: username + password -> tokens."""
