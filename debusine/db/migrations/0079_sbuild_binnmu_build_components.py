from django.db import migrations, transaction
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps


def fix_build_components(
    apps: StateApps,
    schema_editor: BaseDatabaseSchemaEditor,
) -> None:
    """
    Fix build_components for Sbuild binnmu tasks.

    We added validation disallowing arch-indep builds as part of a binnmu.
    This fixes old task_data, to be able to pass the validator.
    """
    WorkRequest = apps.get_model("db", "WorkRequest")
    while True:
        with transaction.atomic():
            models = WorkRequest.objects.select_for_update().filter(
                task_name="sbuild",
                task_data__has_key="binnmu",
                task_data__build_components__has_key="all",
            )[:1000]
            if not models:
                break
            for model in models:
                model.task_data["build_components"].remove("all")
                model.save()


class Migration(migrations.Migration):

    dependencies = [
        ('db', '0078_collectionitem_add_created_removed_by_workflow'),
    ]

    operations = [
        migrations.RunPython(
            fix_build_components, reverse_code=migrations.RunPython.noop
        )
    ]
