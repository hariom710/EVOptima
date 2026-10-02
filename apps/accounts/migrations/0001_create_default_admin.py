from django.contrib.auth.hashers import make_password
from django.db import migrations


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

    # auth/contenttypes must be applied first: `apps.get_model('auth', 'User')`
    # resolves against the *migration state*, which only contains those apps
    # once at least one of their migrations has run. Without these entries the
    # migration raises LookupError: No installed app with label 'auth'.
    dependencies = [
        ('auth', '__first__'),
        ('contenttypes', '__first__'),
    ]

    operations = [
        migrations.RunPython(create_default_admin, remove_default_admin),
    ]
