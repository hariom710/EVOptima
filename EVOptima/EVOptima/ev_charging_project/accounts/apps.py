from django.apps import AppConfig
from django.db.models.signals import post_migrate


def create_default_admin(sender, **kwargs):
    from django.contrib.auth import get_user_model
    User = get_user_model()
    username = 'Admin'
    password = 'Admin@123'
    if not User.objects.filter(username=username).exists():
        user = User.objects.create_user(username=username)
        user.set_password(password)
        user.is_staff = True
        user.is_superuser = True
        user.save()


class AccountsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'accounts'

    def ready(self):
        post_migrate.connect(create_default_admin, sender=self)
