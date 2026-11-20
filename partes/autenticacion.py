from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.models import User
from django.db.models import Q


class UsuarioOEmail(ModelBackend):
    """Cada bombero entra con su email (el de su usuario o el de su ficha) o con su nombre de usuario."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username and "@" in username:
            usuario = (User.objects.filter(Q(email__iexact=username) | Q(integrante__email__iexact=username))
                       .order_by("pk").first())
            if usuario and usuario.check_password(password) and self.user_can_authenticate(usuario):
                return usuario
            return None
        return super().authenticate(request, username=username, password=password, **kwargs)
