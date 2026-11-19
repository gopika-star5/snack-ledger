from functools import wraps
from django.core.exceptions import PermissionDenied
from django.contrib.auth.decorators import login_required

def role_required(group_name):
    """
    Decorator for views that checks whether a user has a specific group role.
    If the user is not authenticated, redirects to login.
    If the user does not belong to the required group, raises PermissionDenied (returns 403).
    """
    def decorator(view_func):
        @login_required
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if request.user.groups.filter(name=group_name).exists():
                return view_func(request, *args, **kwargs)
            raise PermissionDenied(f"Access forbidden: User is not in '{group_name}' role.")
        return _wrapped_view
    return decorator
