from django.urls import path
from sales import views

urlpatterns = [
    path('', views.salesman_home, name='sales_home'),
    path('orders/', views.order_list, name='order_list'),
    path('orders/new/', views.order_create, name='order_create'),
    path('orders/<int:pk>/', views.order_detail, name='order_detail'),
]
