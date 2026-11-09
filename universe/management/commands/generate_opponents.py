from django.core.management.base import BaseCommand, CommandError

from universe.generation.opponents import (
    OpponentGenerationError,
    generate_opponent_pairings,
)


class Command(BaseCommand):
    help = (
        'Write the 1970–1977 project opponent pairings onto an fg-founding-2 '
        'draft that already has corrected memberships and stored schedule '
        'lengths. An identical rerun changes nothing. This command does not '
        'publish the release, assign weeks, or write later seasons.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--league-id', type=int, required=True)
        parser.add_argument('--seed', type=int, required=True)

    def handle(self, *args, **options):
        try:
            outcome = generate_opponent_pairings(
                options['league_id'],
                options['seed'],
            )
        except OpponentGenerationError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(outcome)
