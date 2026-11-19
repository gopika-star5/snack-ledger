from django.urls import path
from production import views

urlpatterns = [
    path('', views.production_home, name='production_home'),
]
