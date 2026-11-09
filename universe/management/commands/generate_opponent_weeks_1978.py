from django.core.management.base import BaseCommand, CommandError

from universe.generation.weeks import OpponentWeekError
from universe.generation.weeks_1978 import generate_opponent_weeks_1978


class Command(BaseCommand):
    help = (
        'Assign each stored 1978–1994 opponent pairing to one of 16 weeks. '
        'Weeks 1–13 are circle rounds and weeks 14–16 are the cross offsets '
        'in formula order. Pairing rows stay as they are. An identical rerun '
        'changes nothing. This command does not publish the release, set a '
        'kickoff, edit the y1978 or y1987 cards, or change 1970–1977 weeks.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--league-id', type=int, required=True)
        parser.add_argument('--seed', type=int, required=True)

    def handle(self, *args, **options):
        try:
            outcome = generate_opponent_weeks_1978(
                options['league_id'],
                options['seed'],
            )
        except OpponentWeekError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(outcome)
