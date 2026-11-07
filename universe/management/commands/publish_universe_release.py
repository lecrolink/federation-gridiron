from django.core.management.base import BaseCommand, CommandError

from universe.publication_service import PublicationRefused, publish_universe_release


class Command(BaseCommand):
    help = (
        'Publish one exact draft universe release, or report unchanged for '
        'an identical published rerun. The command calls the publication '
        'service and does not generate rows, create a timeline, or unpublish.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--league-id', type=int, required=True)
        parser.add_argument('--slug', required=True)
        parser.add_argument('--seed', type=int, required=True)
        parser.add_argument('--generator', required=True)

    def handle(self, *args, **options):
        try:
            result = publish_universe_release(
                slug=options['slug'],
                seed=options['seed'],
                generator_version=options['generator'],
                league_id=options['league_id'],
            )
        except PublicationRefused as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(result.outcome)
