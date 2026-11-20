from django.urls import path
from reports import views

urlpatterns = [
    path('', views.reports_index, name='reports_index'),
    path('home/', views.reports_index, name='reports_home'),
    path('sales-register/', views.sales_register, name='sales_register'),
    path('sales-summary/', views.sales_summary, name='sales_summary'),
    path('gst-summary/', views.gst_summary, name='gst_summary'),
    path('purchase-register/', views.purchase_register, name='purchase_register'),
]
