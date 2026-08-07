from django.db import migrations, models
from django.utils import timezone


def add_v70_columns_safely(apps, schema_editor):
    """Add v7 columns only when absent.

    Some existing QueueUp installations may already contain one or more of
    these columns even though migration 0009 is not recorded. PostgreSQL's
    IF NOT EXISTS makes the migration safe for those partially updated
    databases while still creating every column on a clean installation.
    """
    if schema_editor.connection.vendor != "postgresql":
        # QueueUp's Docker deployment uses PostgreSQL. For another backend,
        # use introspection plus Django's schema editor so tests and local
        # development remain portable.
        existing_by_table = {}
        with schema_editor.connection.cursor() as cursor:
            for table in ("league_userprofile", "league_round", "league_submission"):
                existing_by_table[table] = {
                    column.name
                    for column in schema_editor.connection.introspection.get_table_description(cursor, table)
                }

        definitions = [
            ("UserProfile", "league_userprofile", "approved", models.BooleanField(default=False, help_text="Approved users may enter the league.")),
            ("UserProfile", "league_userprofile", "approved_at", models.DateTimeField(blank=True, null=True)),
            ("UserProfile", "league_userprofile", "voting_guide_seen", models.BooleanField(default=False)),
            ("UserProfile", "league_userprofile", "submission_rules_accepted_at", models.DateTimeField(blank=True, null=True)),
            ("Round", "league_round", "goes_live_at", models.DateTimeField(blank=True, help_text="Players cannot see this round before this time.", null=True)),
            ("Submission", "league_submission", "explicit", models.BooleanField(default=False)),
        ]
        for model_name, table, field_name, field in definitions:
            if field_name in existing_by_table[table]:
                continue
            model = apps.get_model("league", model_name)
            field.set_attributes_from_name(field_name)
            field.model = model
            schema_editor.add_field(model, field)
        return

    schema_editor.execute(
        """
        ALTER TABLE league_userprofile
            ADD COLUMN IF NOT EXISTS approved boolean NOT NULL DEFAULT false,
            ADD COLUMN IF NOT EXISTS approved_at timestamp with time zone NULL,
            ADD COLUMN IF NOT EXISTS voting_guide_seen boolean NOT NULL DEFAULT false,
            ADD COLUMN IF NOT EXISTS submission_rules_accepted_at timestamp with time zone NULL;

        ALTER TABLE league_round
            ADD COLUMN IF NOT EXISTS goes_live_at timestamp with time zone NULL;

        ALTER TABLE league_submission
            ADD COLUMN IF NOT EXISTS explicit boolean NOT NULL DEFAULT false;

        ALTER TABLE league_userprofile ALTER COLUMN approved DROP DEFAULT;
        ALTER TABLE league_userprofile ALTER COLUMN voting_guide_seen DROP DEFAULT;
        ALTER TABLE league_submission ALTER COLUMN explicit DROP DEFAULT;
        """
    )


def approve_existing_users(apps, schema_editor):
    Profile = apps.get_model("league", "UserProfile")
    User = apps.get_model("auth", "User")
    now = timezone.now()
    for user in User.objects.all().iterator():
        profile, _ = Profile.objects.get_or_create(user_id=user.pk)
        profile.approved = True
        profile.approved_at = now
        profile.save(update_fields=["approved", "approved_at"])


class Migration(migrations.Migration):
    dependencies = [("league", "0008_v555_engagement")]
    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunPython(add_v70_columns_safely, migrations.RunPython.noop)],
            state_operations=[
                migrations.AddField(
                    model_name="userprofile",
                    name="approved",
                    field=models.BooleanField(default=False, help_text="Approved users may enter the league."),
                ),
                migrations.AddField(
                    model_name="userprofile",
                    name="approved_at",
                    field=models.DateTimeField(blank=True, null=True),
                ),
                migrations.AddField(
                    model_name="userprofile",
                    name="voting_guide_seen",
                    field=models.BooleanField(default=False),
                ),
                migrations.AddField(
                    model_name="userprofile",
                    name="submission_rules_accepted_at",
                    field=models.DateTimeField(blank=True, null=True),
                ),
                migrations.AddField(
                    model_name="round",
                    name="goes_live_at",
                    field=models.DateTimeField(blank=True, help_text="Players cannot see this round before this time.", null=True),
                ),
                migrations.AddField(
                    model_name="submission",
                    name="explicit",
                    field=models.BooleanField(default=False),
                ),
            ],
        ),
        migrations.RunPython(approve_existing_users, migrations.RunPython.noop),
    ]
