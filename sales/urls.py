from django.urls import path
from sales import views

urlpatterns = [
    path('', views.salesman_home, name='sales_home'),
]
