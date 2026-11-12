from django.core.management.base import BaseCommand, CommandError

from universe.generation.historical_1970 import (
    HistoricalScheduleError,
    load_historical_schedule_1970,
)


class Command(BaseCommand):
    help = (
        'Load the immutable 1970 historical schedule template from the '
        'checked-in regular-season source. An exact rerun writes nothing. '
        'This command does not assign Federation franchises, create timeline '
        'fixtures, or edit project schedules.'
    )

    def handle(self, *args, **options):
        try:
            outcome = load_historical_schedule_1970()
        except HistoricalScheduleError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(outcome)
