import importlib

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db import migrations
from django.test import TestCase

from leagues.models import League
from universe.generation.opening_players import y1970_values
from universe.models import UniverseRelease
from universe.rules import PLAYOFF_FORMATS, EraRuleProfile

playoff_migration = importlib.import_module(
    'universe.migrations.0006_era_playoff_fill_to_six'
)


ACCEPTED_FORMATS = (
    'eight_team_3_and_1',
    'eight_team_2_and_2',
    'twelve_team_fill_to_six',
    'twelve_team_4_and_2',
)


class PlayoffFormatTests(TestCase):
    def test_settings_cannot_reach_the_configured_database(self):
        database = settings.DATABASES['default']
        self.assertEqual(database['ENGINE'], 'django.db.backends.sqlite3')
        self.assertIn('memory', database['NAME'])
        self.assertNotIn('mysql', database['ENGINE'])
        self.assertNotIn('pythonanywhere', str(settings.DATABASES))
        self.assertNotIn('lecrolink', str(settings.DATABASES))
        self.assertFalse(database.get('HOST'))
        self.assertEqual(connection.vendor, 'sqlite')
        self.assertFalse(connection.settings_dict.get('HOST'))

    def test_migration_changes_choices_without_writing_profiles(self):
        operations = playoff_migration.Migration.operations
        self.assertEqual(
            [type(operation) for operation in operations],
            [
                migrations.RemoveConstraint,
                migrations.AlterField,
                migrations.AddConstraint,
            ],
        )
        self.assertFalse(
            any(
                isinstance(operation, (migrations.RunPython, migrations.RunSQL))
                for operation in operations
            )
        )
        constraint = operations[2].constraint
        self.assertEqual(constraint.name, 'universe_era_playoff')
        self.assertEqual(
            constraint.condition.children,
            [('playoff_format__in', ACCEPTED_FORMATS)],
        )

    def test_known_formats_save_and_an_unknown_format_is_rejected(self):
        self.assertEqual(PLAYOFF_FORMATS, ACCEPTED_FORMATS)
        self.assertEqual(y1970_values()['playoff_format'], 'eight_team_3_and_1')

        league = League.objects.create(
            name='Playoff Format League',
            slug='playoff-format-league',
            tier=1,
        )
        release = UniverseRelease.objects.create(
            slug='playoff-format-release',
            root_league=league,
            generator_version='test',
            seed=1970,
            founding_franchise_count=26,
            expansion_schedule_version=3,
            first_season_year=1970,
            season_count=4,
            last_season_year=1973,
        )
        profile = EraRuleProfile(release=release, **y1970_values())
        profile.full_clean()
        profile.save()

        for playoff_format in ACCEPTED_FORMATS:
            profile.playoff_format = playoff_format
            profile.full_clean()
            profile.save(update_fields=['playoff_format'])
            profile.refresh_from_db()
            self.assertEqual(profile.playoff_format, playoff_format)

        profile.playoff_format = 'not_a_playoff_format'
        with self.assertRaises(ValidationError):
            profile.save(update_fields=['playoff_format'])
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                EraRuleProfile.objects.filter(pk=profile.pk).update(
                    playoff_format='not_a_playoff_format',
                )
        profile.refresh_from_db()
        self.assertEqual(profile.playoff_format, 'twelve_team_4_and_2')
