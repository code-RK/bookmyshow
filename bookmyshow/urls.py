"""
URL configuration for bookmyshow project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import include, path
from .views import LivenessView, ReadinessView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/v1/auth/', include('accounts.urls')),
    # Mounted at api/v1/ rather than api/v1/shows: a `shows/` prefix would
    # need the trailing slash for the detail route, which makes APPEND_SLASH
    # redirect POST /api/v1/shows and drop the request body. The `shows`
    # segment therefore lives in show/urls.py.
    path('api/v1/', include('show.urls')),
    # No leading slash: Django strips it before matching, so '/health/live'
    # would never match.
    path('health/live', LivenessView.as_view(), name="live-check"),
    path('health/ready', ReadinessView.as_view(), name="ready-check"),
    # GET /metrics, scraped by Prometheus (see docker/prometheus.yml).
    path("", include("django_prometheus.urls")),
]
