from django.core.management.base import BaseCommand, CommandError

from universe.generation.opponents import OpponentGenerationError
from universe.generation.opponents_1995 import generate_opponent_pairings_1995


class Command(BaseCommand):
    help = (
        'Write the 1995–1998 project opponent pairings onto an fg-founding-2 '
        'draft that already has corrected memberships. The stored schedule '
        'length must be 16. An identical rerun changes nothing. This command '
        'does not publish the release, assign weeks, edit the y1995 card, or '
        'change 1970–1994 pairings. Seasons after 1998 are left alone.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--league-id', type=int, required=True)
        parser.add_argument('--seed', type=int, required=True)

    def handle(self, *args, **options):
        try:
            outcome = generate_opponent_pairings_1995(
                options['league_id'],
                options['seed'],
            )
        except OpponentGenerationError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(outcome)
