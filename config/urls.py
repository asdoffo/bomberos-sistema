"""
URL configuration for config project.

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
from django.conf import settings
from django.conf.urls import include
from django.contrib.auth.decorators import login_required
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path, re_path
from django.views.static import serve

from partes import views as partes_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('partes/', include('partes.urls')),
    # Cambiar la contraseña desde Mi perfil, con las pantallas del sistema (las de registration/ del admin
    # taparían las nuestras). Van antes de las de django.contrib.auth para usar estas plantillas.
    path('accounts/password_change/', auth_views.PasswordChangeView.as_view(
        template_name='partes/cambiar_clave.html'), name='password_change'),
    path('accounts/password_change/done/', auth_views.PasswordChangeDoneView.as_view(
        template_name='partes/cambiar_clave_listo.html'), name='password_change_done'),
    path('accounts/', include('django.contrib.auth.urls')),
    path('', partes_views.menu, name='menu'),
    # Carga sin señal: el service worker tiene que estar en la raíz para cubrir todo el sitio.
    path('sw.js', partes_views.service_worker, name='service_worker'),
    path('sin-conexion/', partes_views.sin_conexion, name='sin_conexion'),
]

# Fotos de siniestros y de integrantes: solo para usuarios logueados (también en producción).
urlpatterns += [
    re_path(r'^media/(?P<path>.*)$', login_required(serve), {'document_root': settings.MEDIA_ROOT}),
]
