from django.db import connection
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status


@api_view(['GET'])
@permission_classes([AllowAny])
def health_check(request):
    """
    Health check endpoint reporting system and database connectivity.
    Per specification: Unauthenticated, returns 200 and a JSON body reporting database connectivity.
    """
    db_connected = False
    details = ""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1;")
            cursor.fetchone()
        db_connected = True
    except Exception as exc:
        details = str(exc)

    payload = {
        "status": "healthy" if db_connected else "degraded",
        "database": {
            "connected": db_connected,
            "engine": connection.settings_dict.get("ENGINE", "unknown"),
        },
    }
    if not db_connected and details:
        payload["database"]["error"] = details

    return Response(payload, status=status.HTTP_200_OK)
