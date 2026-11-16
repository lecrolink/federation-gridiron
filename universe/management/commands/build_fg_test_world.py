"""Verify or build the fg-1970 world in the PythonAnywhere test database.

The command refuses to run unless Django's default database name is exactly
lecrolink$fg_test. lecrolink$fg is refused on its own. Service return values
decide the machine-readable world_result line.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from universe.world_build import (
    PRODUCTION_DATABASE_NAME,
    TEST_DATABASE_NAME,
    WorldBuildRefused,
    execute,
)


class Command(BaseCommand):
    help = (
        'Verify, build, publish, or start the fg-1970 test world. '
        'The database name must be exactly lecrolink$fg_test.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--stage',
            required=True,
            choices=('VERIFY', 'BUILD_DRAFT', 'PUBLISH', 'START_1970'),
        )

    def handle(self, *args, **options):
        name = settings.DATABASES['default']['NAME']
        if name == 'lecrolink$fg' or name == PRODUCTION_DATABASE_NAME:
            self.stdout.write('world_result=failure')
            raise CommandError(
                'Refusing to build: the production database is not a target.'
            )
        if name != 'lecrolink$fg_test' or name != TEST_DATABASE_NAME:
            self.stdout.write('world_result=failure')
            raise CommandError(
                'Refusing to build: Django database name is not lecrolink$fg_test.'
            )
        try:
            execute(self.stdout, options['stage'])
        except WorldBuildRefused as exc:
            raise CommandError(str(exc)) from exc
        except CommandError:
            raise
        except Exception as exc:
            raise CommandError(str(exc)) from exc
