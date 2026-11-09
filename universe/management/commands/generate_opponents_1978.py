from django.core.management.base import BaseCommand, CommandError

from universe.generation.opponents import OpponentGenerationError
from universe.generation.opponents_1978 import generate_opponent_pairings_1978


class Command(BaseCommand):
    help = (
        'Write the 1978–1989 project opponent pairings onto an fg-founding-2 '
        'draft that already has corrected memberships. The stored schedule '
        'length must be 16. An identical rerun changes nothing. This command '
        'does not publish the release, assign weeks, edit era cards, or '
        'change 1970–1977 pairings.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--league-id', type=int, required=True)
        parser.add_argument('--seed', type=int, required=True)

    def handle(self, *args, **options):
        try:
            outcome = generate_opponent_pairings_1978(
                options['league_id'],
                options['seed'],
            )
        except OpponentGenerationError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(outcome)
