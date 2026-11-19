from django.shortcuts import render
from core.decorators import role_required

@role_required('production')
def production_home(request):
    """Production supervisor role home page."""
    return render(request, 'production/production_home.html')
