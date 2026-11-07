from django.core.management.base import BaseCommand, CommandError

from timelines.services.timeline_start import TimelineStartRefused, start_timeline


class Command(BaseCommand):
    help = (
        'Start one human 1970 campaign on a published fg-founding-2 release, '
        'or report unchanged when that campaign already matches. The command '
        'calls the timeline service and does not publish a release.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--slug', required=True)
        parser.add_argument('--seed', type=int, required=True)
        parser.add_argument('--generator', required=True)
        parser.add_argument('--user-id', type=int, required=True)
        parser.add_argument('--name', required=True)
        parser.add_argument('--franchise-slug', required=True)

    def handle(self, *args, **options):
        try:
            result = start_timeline(
                slug=options['slug'],
                seed=options['seed'],
                generator_version=options['generator'],
                user_id=options['user_id'],
                name=options['name'],
                franchise_slug=options['franchise_slug'],
            )
        except TimelineStartRefused as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(result.outcome)
        self.stdout.write('timeline_id=%s' % result.timeline_id)
        self.stdout.write('participant_id=%s' % result.participant_id)
        self.stdout.write('control_id=%s' % result.control_id)
        self.stdout.write('season_id=%s' % result.season_id)
        self.stdout.write('directive_id=%s' % result.directive_id)
        self.stdout.write('players=%s' % result.player_count)
        self.stdout.write('ratings=%s' % result.rating_count)
        self.stdout.write('memberships=%s' % result.membership_count)
        self.stdout.write('rostered=%s' % result.rostered_count)
        self.stdout.write('free_agents=%s' % result.free_agent_count)
