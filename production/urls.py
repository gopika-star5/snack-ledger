from django.urls import path
from production import views

urlpatterns = [
    path('', views.production_home, name='production_home'),
    path('purchases/', views.purchase_list, name='purchase_list'),
    path('purchases/new/', views.purchase_create, name='purchase_create'),
    path('runs/', views.run_list, name='run_list'),
    path('runs/new/', views.run_create, name='run_create'),
    path('runs/<int:pk>/', views.run_result, name='run_result'),
    path('runs/<int:pk>/detail/', views.run_result, name='run_detail'),
]
