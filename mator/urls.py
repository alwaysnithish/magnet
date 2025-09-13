"""
URL configuration for mator project.
"""
from django.contrib import admin
from django.urls import path
from django.conf import settings
from django.conf.urls.static import static
from . import views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', views.download_view, name='download'),
    path('download/', views.download_view, name='download_alt'),
    path('status/', views.status_view, name='status'),
    path('health/', views.status_view, name='health'),
    path('api/validate-magnet/', views.validate_magnet, name='validate_magnet'),
]

# Serve static files during development
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
