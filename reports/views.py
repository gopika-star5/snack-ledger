from django.shortcuts import render
from core.decorators import role_required

@role_required('accountant')
def accountant_home(request):
    """Accountant / Auditor role home page."""
    return render(request, 'reports/accountant_home.html')
