from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import status
from django.db import connection


class LivenessView(APIView):
    permission_classes = []

    def get(self, request):
        return Response(
            {"status": "ok"},
            status=status.HTTP_200_OK
        )

class ReadinessView(APIView):
    permission_classes = []

    def get(self, request):
        try:
            connection.ensure_connection()

            return Response(
                {
                    "status": "ready",
                    "database": "ok"
                },
                status=status.HTTP_200_OK
            )

        except Exception:
            return Response(
                {
                    "status": "not_ready",
                    "database": "unavailable"
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE
            )