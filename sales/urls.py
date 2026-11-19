from django.urls import path
from sales import views

urlpatterns = [
    path('', views.salesman_home, name='sales_home'),
    path('orders/', views.order_list, name='order_list'),
    path('orders/new/', views.order_create, name='order_create'),
    path('orders/<int:pk>/', views.order_detail, name='order_detail'),
    path('admin/pending-orders/', views.admin_pending_orders, name='admin_pending_orders'),
    path('pending-orders/', views.admin_pending_orders, name='pending_orders'),
    path('admin/orders/<int:pk>/', views.admin_order_detail, name='admin_order_detail'),
    path('orders/<int:pk>/review/', views.admin_order_detail, name='admin_order_review'),
]
