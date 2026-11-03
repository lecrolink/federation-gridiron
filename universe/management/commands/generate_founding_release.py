from django.core.management.base import BaseCommand, CommandError

from universe.generation.founding import FoundingGenerationError, generate_founding_release


class Command(BaseCommand):
    help = (
        'Fill an existing draft universe release with the 24 founding '
        'franchises, their 1970 memberships, and the initial GM and '
        'head-coach pools.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--league-id', type=int, required=True)
        parser.add_argument('--seed', type=int, required=True)

    def handle(self, *args, **options):
        try:
            outcome = generate_founding_release(options['league_id'], options['seed'])
        except FoundingGenerationError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(outcome)
