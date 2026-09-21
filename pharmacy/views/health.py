from django.db import connection
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response


@extend_schema(
    tags=["System"],
    summary="Liveness and database connectivity",
    responses={200: None, 503: None},
)
@api_view(["GET"])
@authentication_classes([])
@permission_classes([])
def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        database = "ok"
    except Exception:
        database = "unavailable"

    payload = {"status": "ok" if database == "ok" else "degraded", "database": database}
    code = status.HTTP_200_OK if database == "ok" else status.HTTP_503_SERVICE_UNAVAILABLE
    return Response(payload, status=code)
