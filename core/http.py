"""Small helpers for JSON endpoints."""
import json
from functools import wraps

from django.http import JsonResponse


def bad(message, status=400):
    return JsonResponse({"ok": False, "error": message}, status=status)


def client_ip(request):
    return request.META.get("REMOTE_ADDR") or None


def post_json(view):
    """Require POST with a JSON object body; passes it to the view as `data`."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if request.method != "POST":
            return bad("POST required.", 405)
        try:
            data = json.loads(request.body.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            return bad("Invalid JSON.")
        if not isinstance(data, dict):
            return bad("Invalid JSON.")
        return view(request, *args, data=data, **kwargs)

    return wrapper


def login_required_json(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return bad("Please log in.", 401)
        return view(request, *args, **kwargs)

    return wrapper
