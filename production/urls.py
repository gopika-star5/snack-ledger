from django.urls import path
from production import views

urlpatterns = [
    path('', views.production_home, name='production_home'),
    path('purchases/', views.purchase_list, name='purchase_list'),
    path('purchases/new/', views.purchase_create, name='purchase_create'),
]
