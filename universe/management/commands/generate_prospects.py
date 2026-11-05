from django.core.management.base import BaseCommand, CommandError

from universe.generation.prospects import (
    ProspectGenerationError,
    generate_prospects,
)


class Command(BaseCommand):
    help = (
        'Write the 11 later era profiles, 60 draft classes, and 19,777 '
        'prospects onto an existing draft release that already has its '
        'fg-founding-2 schedule and opening players. An identical rerun '
        'changes nothing. This command does not publish the release.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--league-id', type=int, required=True)
        parser.add_argument('--seed', type=int, required=True)

    def handle(self, *args, **options):
        try:
            outcome = generate_prospects(
                options['league_id'],
                options['seed'],
            )
        except ProspectGenerationError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(outcome)
