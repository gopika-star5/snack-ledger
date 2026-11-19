from django.urls import path
from reports import views

urlpatterns = [
    path('', views.accountant_home, name='reports_home'),
]
