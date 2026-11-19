from django.contrib import admin
from django.urls import path, include
from core import views as core_views
from sales import views as sales_views
from production import views as prod_views
from reports import views as reports_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('core.urls')),
    path('salesman/', sales_views.salesman_home, name='salesman_home'),
    path('sales/', include('sales.urls')),
    path('production/', prod_views.production_home, name='production_home'),
    path('production-app/', include('production.urls')),
    path('accountant/', reports_views.accountant_home, name='accountant_home'),
    path('reports/', include('reports.urls')),
    path('admin-home/', core_views.admin_home, name='admin_home'),
    path('owner/', core_views.owner_home, name='owner_home'),
]
