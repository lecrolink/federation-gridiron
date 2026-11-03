from django.core.management.base import BaseCommand, CommandError

from universe.generation.founding import FoundingGenerationError, generate_founding_release
from universe.generation.schedule import ScheduleGenerationError, generate_schedule_release


class Command(BaseCommand):
    help = (
        'Fill an existing draft universe release. The default generator is '
        'fg-founding-1, which writes the 24 founding franchises, their 1970 '
        'memberships, and the initial GM and head-coach pools. '
        'fg-founding-2 writes the 26-club schedule through 2029.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--league-id', type=int, required=True)
        parser.add_argument('--seed', type=int, required=True)
        parser.add_argument(
            '--generator',
            choices=('fg-founding-1', 'fg-founding-2'),
            default='fg-founding-1',
        )

    def handle(self, *args, **options):
        generator = options['generator']
        try:
            if generator == 'fg-founding-1':
                outcome = generate_founding_release(options['league_id'], options['seed'])
            else:
                outcome = generate_schedule_release(options['league_id'], options['seed'])
        except (FoundingGenerationError, ScheduleGenerationError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(outcome)
