from django.core.management.base import BaseCommand, CommandError

from universe.generation.opening_players import (
    OpeningGenerationError,
    generate_opening_players,
)


class Command(BaseCommand):
    help = (
        'Write the 1,378 opening players onto an existing draft release that '
        'already has its fg-founding-2 schedule. An identical rerun changes '
        'nothing. This command does not publish the release or write prospects.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--league-id', type=int, required=True)
        parser.add_argument('--seed', type=int, required=True)

    def handle(self, *args, **options):
        try:
            outcome = generate_opening_players(
                options['league_id'],
                options['seed'],
            )
        except OpeningGenerationError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(outcome)
