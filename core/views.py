from django.shortcuts import render, redirect
from django.contrib.auth import logout
from django.contrib.auth.views import LoginView
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.core.exceptions import PermissionDenied

from core.decorators import role_required


def get_role_home_url(user):
    """Determine the home page URL based on user's group."""
    if user.groups.filter(name='salesman').exists():
        return reverse('salesman_home')
    elif user.groups.filter(name='admin').exists():
        return reverse('admin_home')
    elif user.groups.filter(name='production').exists():
        return reverse('production_home')
    elif user.groups.filter(name='accountant').exists():
        return reverse('accountant_home')
    elif user.groups.filter(name='owner').exists():
        return reverse('owner_home')
    return reverse('login')


class CustomLoginView(LoginView):
    template_name = 'registration/login.html'
    redirect_authenticated_user = True

    def get_success_url(self):
        redirect_to = self.redirect_field_name
        next_url = self.request.POST.get(redirect_to, self.request.GET.get(redirect_to, ''))
        if next_url and url_has_allowed_host_and_scheme(url=next_url, allowed_hosts={self.request.get_host()}):
            return next_url
        return get_role_home_url(self.request.user)


def logout_view(request):
    """Log out user and redirect to login page (supports both GET and POST)."""
    logout(request)
    return redirect('login')


@login_required
def role_redirect(request):
    """Redirect logged in user to their designated role home page."""
    destination = get_role_home_url(request.user)
    if destination == reverse('login'):
        raise PermissionDenied("User does not have an assigned role group.")
    return redirect(destination)


@role_required('admin')
def admin_home(request):
    """Admin role home page."""
    return render(request, 'core/admin_home.html')


@role_required('owner')
def owner_home(request):
    """Owner role home page."""
    return render(request, 'core/owner_home.html')
