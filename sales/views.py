from django.shortcuts import render
from core.decorators import role_required

@role_required('salesman')
def salesman_home(request):
    """Salesman role home page."""
    return render(request, 'sales/salesman_home.html')
