from django.db import migrations
from django.contrib.auth.hashers import make_password


def create_default_admin(apps, schema_editor):
    # Use historical model; do not call instance methods like set_password
    User = apps.get_model('auth', 'User')
    username = 'Admin'
    password = 'Admin@123'
    if not User.objects.filter(username=username).exists():
        User.objects.create(
            username=username,
            password=make_password(password),
            is_staff=True,
            is_superuser=True,
        )


def remove_default_admin(apps, schema_editor):
    User = apps.get_model('auth', 'User')
    User.objects.filter(username='Admin').delete()


class Migration(migrations.Migration):

    dependencies = []

    operations = [
        migrations.RunPython(create_default_admin, remove_default_admin),
    ]
