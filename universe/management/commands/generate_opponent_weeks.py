from django.core.management.base import BaseCommand, CommandError

from universe.generation.weeks import OpponentWeekError, generate_opponent_weeks


class Command(BaseCommand):
    help = (
        'Assign each stored 1970–1977 opponent pairing to one of 14 weeks. '
        'Pairing rows stay as they are. An identical rerun changes nothing. '
        'This command does not publish the release, set a kickoff, or write '
        'seasons after 1977.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--league-id', type=int, required=True)
        parser.add_argument('--seed', type=int, required=True)

    def handle(self, *args, **options):
        try:
            outcome = generate_opponent_weeks(
                options['league_id'],
                options['seed'],
            )
        except OpponentWeekError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(outcome)
