from django.urls import path
from core import views

urlpatterns = [
    path('', views.role_redirect, name='root'),
    path('login/', views.CustomLoginView.as_view(), name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('dashboard/', views.role_redirect, name='role_redirect'),
    path('benchmark/', views.benchmark_view, name='benchmark'),
]
