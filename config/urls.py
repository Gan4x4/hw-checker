"""config URL Configuration."""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path


admin.site.site_header = "Homework administration"  # Top banner
admin.site.site_title = "HW Checker"                 # Browser tab suffix
admin.site.index_title = "Dashboard"                # Homepage heading

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("quiz.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
