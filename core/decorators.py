from functools import wraps
from django.core.exceptions import PermissionDenied
from django.contrib.auth.decorators import login_required

def role_required(*group_names):
    """
    Decorator for views that checks whether a user has any of the specified group roles.
    If the user is not authenticated, redirects to login.
    If the user does not belong to any required group, raises PermissionDenied (returns 403).
    """
    def decorator(view_func):
        @login_required
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if request.user.is_superuser or request.user.groups.filter(name__in=group_names).exists():
                return view_func(request, *args, **kwargs)
            roles_str = ", ".join(f"'{g}'" for g in group_names)
            raise PermissionDenied(f"Access forbidden: User is not in {roles_str} role.")
        return _wrapped_view
    return decorator
