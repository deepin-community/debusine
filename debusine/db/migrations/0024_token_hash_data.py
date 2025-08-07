import hashlib

from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.exceptions import IrreversibleError
from django.db.migrations.state import StateApps


def _generate_hash(secret: str) -> str:
    """Hash the given secret."""
    return hashlib.sha256(secret.encode()).hexdigest()


def update_hash(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    Token = apps.get_model("db", "Token")

    for token in Token.objects.all():
        token.hash = _generate_hash(token.key)
        token.save()


def revert_hash(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    Token = apps.get_model("db", "Token")

    if Token.objects.all():
        raise IrreversibleError("can't revert token hash migration")


class Migration(migrations.Migration):

    dependencies = [("db", "0023_token_hash_initial")]
    replaces = [("db", "0023_token_hash")]

    operations = [
        migrations.RunPython(update_hash, revert_hash),
    ]
