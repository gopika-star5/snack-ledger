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

    # Invoice & Dispatch routes
    path('invoices/', views.invoice_list, name='invoice_list'),
    path('admin/invoices/', views.invoice_list, name='admin_invoices'),
    path('orders/<int:pk>/create-invoice/', views.create_invoice_from_order, name='create_invoice'),
    path('orders/<int:pk>/invoice/create/', views.create_invoice_from_order, name='order_create_invoice'),
    path('invoices/<int:pk>/', views.invoice_detail, name='invoice_detail'),
    path('admin/invoices/<int:pk>/', views.invoice_detail, name='admin_invoice_detail'),
    path('invoices/<int:pk>/pdf/', views.invoice_pdf, name='invoice_pdf'),
    path('invoices/<int:pk>/dispatch/', views.invoice_dispatch, name='invoice_dispatch'),
    path('invoices/<int:pk>/cancel/', views.invoice_cancel, name='invoice_cancel'),

    # Payment & Receivables routes
    path('payments/record/', views.record_payment_view, name='record_payment'),
    path('payments/new/', views.record_payment_view, name='payment_create'),
    path('admin/payments/record/', views.record_payment_view, name='admin_record_payment'),
    path('outstanding/', views.customer_outstanding_list, name='customer_outstanding'),
    path('customers/outstanding/', views.customer_outstanding_list, name='outstanding_list'),
    path('admin/outstanding/', views.customer_outstanding_list, name='admin_customer_outstanding'),
]
