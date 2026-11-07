from functools import wraps

from django.shortcuts import redirect, render

from . import audit
from .http import client_ip


def perm_required(*codes, area="manager"):
    """HTML views: the user must be signed in and hold at least one of the permission codes."""

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect(f"/app/{area}/")
            if not any(request.user.can(code) for code in codes):
                audit.record("auth.area_denied", actor=request.user, object_type=request.path[:60],
                             ip=client_ip(request))
                return render(request, "core/forbidden.html", status=403)
            return view(request, *args, **kwargs)

        return wrapper

    return decorator
