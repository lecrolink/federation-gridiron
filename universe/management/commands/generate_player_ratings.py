from django.core.management.base import BaseCommand, CommandError

from universe.generation.player_rating_profiles import (
    PlayerRatingGenerationError,
    generate_player_ratings,
)


class Command(BaseCommand):
    help = (
        'Write one PlayerRatingProfile for each of the 21,155 playable '
        'players on an existing fg-founding-2 draft release. An identical '
        'rerun changes nothing. This command does not publish the release.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--league-id', type=int, required=True)
        parser.add_argument('--seed', type=int, required=True)

    def handle(self, *args, **options):
        try:
            outcome = generate_player_ratings(
                options['league_id'],
                options['seed'],
            )
        except PlayerRatingGenerationError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(outcome)
