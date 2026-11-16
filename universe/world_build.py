"""Staged reads and writes for the PythonAnywhere fg-1970 test world.

The database name is checked before any row is read. Writer results come
from the service return values. Human log text is not inspected to decide
whether anything was written.
"""

import re

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connections
from django.db.migrations.executor import MigrationExecutor
from django.db.models import Count

from leagues.models import League
from timelines.models import (
    PlayerSeasonRating,
    RosterMembership,
    ScheduleBye,
    ScheduleGame,
    ScheduleWeekAssignment,
    Timeline,
    TimelineScheduleSlotAssignment,
)
from universe.fingerprints import release_digests
from universe.generation.approved_names import (
    APPROVED_FRANCHISE_NAMES,
    REPLACED,
    display_name,
    validate_approved_names,
)
from universe.generation.historical_1970 import (
    _checked_template,
    _stored_state,
    load_historical_schedule_1970,
)
from universe.generation.opening_players import generate_opening_players
from universe.generation.player_rating_profiles import generate_player_ratings
from universe.generation.prospects import generate_prospects
from universe.generation.schedule import (
    DEBUT_YEAR,
    GENERATOR_VERSION,
    generate_schedule_release,
    membership_rows,
)
from universe.models import (
    FranchiseSeasonMembership,
    ProvisionalFranchiseName,
    UniverseFranchise,
    UniverseRelease,
)
from universe.people import PlayerRatingProfile, UniversePlayer
from universe.publication import evaluate_release_publication
from universe.publication_service import publish_universe_release
from universe.rules import ExpansionClub
from timelines.services.historical_1970 import (
    generate_historical_timeline_schedule_1970,
    historical_fixture_state,
)
from timelines.services.timeline_start import start_timeline


TEST_DATABASE_NAME = 'lecrolink$fg_test'
PRODUCTION_DATABASE_NAME = 'lecrolink$fg'
STAGES = ('VERIFY', 'BUILD_DRAFT', 'PUBLISH', 'START_1970')
CAMPAIGN_NAME = 'Opening 1970'
FRANCHISE_SLUG = 'founding-01'
RELEASE_SLUG = 'fg-1970'
LEAGUE_SLUG = 'federation'
LEAGUE_NAME = 'Federation'
USERNAME = 'lecrolink'
HEX_DIGEST = re.compile(r'^[0-9a-f]{64}$')
WRITER_RESULTS = ('created', 'rebuilt', 'unchanged')
WRITE_RESULTS = ('created', 'rebuilt', 'published')

EXPECTED = {
    'franchises': 32,
    'memberships': 1797,
    'replaced_names': 32,
    'expansion_events': 4,
    'expansion_clubs': 6,
    'staff_identities': 200,
    'general_managers': 100,
    'head_coaches': 100,
    'seated_general_managers': 26,
    'seated_head_coaches': 26,
    'colleges': 64,
    'era_profiles': 12,
    'draft_classes': 60,
    'opening_players': 1378,
    'opening_assignments': 1378,
    'starters': 494,
    'backups': 546,
    'taxi': 208,
    'opening_free_agents': 130,
    'prospects': 19777,
    'draftable_prospects': 16183,
    'undrafted_prospects': 3594,
    'players': 21155,
    'rating_profiles': 21155,
}
SEASON_SIZES = {
    1970: 26,
    1975: 26,
    1976: 28,
    1994: 28,
    1995: 30,
    1998: 30,
    1999: 31,
    2000: 31,
    2001: 31,
    2002: 32,
    2029: 32,
}
SCHEDULE_KEYS = (
    'franchises',
    'memberships',
    'replaced_names',
    'expansion_events',
    'expansion_clubs',
    'staff_identities',
    'general_managers',
    'head_coaches',
    'seated_general_managers',
    'seated_head_coaches',
)
OPENING_KEYS = (
    'colleges',
    'opening_players',
    'opening_assignments',
    'starters',
    'backups',
    'taxi',
    'opening_free_agents',
)
PROSPECT_KEYS = (
    'prospects',
    'draftable_prospects',
    'undrafted_prospects',
    'draft_classes',
)
TIMELINE_COUNTS = {
    'participants': 1,
    'controls': 1,
    'seasons': 1,
    'directives': 1,
    'timeline_players': 1378,
    'season_ratings': 1378,
    'roster_memberships': 1378,
    'rostered': 1248,
    'free_agents': 130,
}
FIXTURE_COUNTS = {
    'slot_assignments': 26,
    'games': 182,
    'weeks': 182,
    'byes': 0,
}


class WorldBuildRefused(Exception):
    """The stage stopped. Rows already committed by an earlier writer stay."""


def assert_test_database():
    """Refuse every database except the literal PythonAnywhere test name."""
    name = settings.DATABASES['default']['NAME']
    if name == PRODUCTION_DATABASE_NAME:
        raise WorldBuildRefused(
            'Refusing to build: the production database is not a target.'
        )
    if name != TEST_DATABASE_NAME:
        raise WorldBuildRefused(
            'Refusing to build: Django database name is not lecrolink$fg_test.'
        )


def execute(stdout, stage):
    """Run one stage. The last stdout line is the machine-readable result."""
    result = 'failure'
    try:
        result = _run(stdout, stage)
    except WorldBuildRefused:
        result = 'failure'
        raise
    finally:
        stdout.write(f'world_result={result}')


def _run(stdout, stage):
    assert_test_database()
    if stage not in STAGES:
        raise WorldBuildRefused('The stage is not legal.')
    pending = pending_migration_count()
    stdout.write(f'migrations_pending={pending}')
    if pending:
        raise WorldBuildRefused(
            f'{pending} schema changes are pending. Nothing was applied.'
        )
    league, release, user = resolve_identity()
    stdout.write(f'database={TEST_DATABASE_NAME}')
    stdout.write(f'league_id={league.pk}')
    stdout.write(f'release_id={release.pk}')
    stdout.write(f'user_id={user.pk}')
    view = inspect_world(release, user)
    stdout.write(f'classification={view["classification"]}')
    stdout.write(f'template={view["template"]}')
    stdout.write(f'fixtures={view["fixtures"]}')
    stdout.write(
        f'names_exact={"yes" if view["snapshot"]["names_exact"] else "no"}'
    )
    stdout.write(
        f'alignments_exact={"yes" if view["snapshot"]["alignments_exact"] else "no"}'
    )
    if view['timeline_count'] == 1:
        for key, count in fixture_counts(Timeline.objects.get(release=release)).items():
            stdout.write(f'count.fixture.{key}={count}')
    if view['timeline_count'] > 1:
        raise WorldBuildRefused('A different timeline exists. It was left unchanged.')
    if stage == 'VERIFY':
        write_counts(stdout, view['snapshot'])
        return 'readonly'
    if stage == 'BUILD_DRAFT':
        return build_draft(stdout, league, release, view)
    if stage == 'PUBLISH':
        return publish_draft(stdout, league, release, view)
    return start_1970(stdout, release, user, view)


def pending_migration_count():
    """Count unapplied schema changes. This does not apply them."""
    executor = MigrationExecutor(connections['default'])
    plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    return len(plan)


def resolve_identity():
    """Find the Federation league, fg-1970 release, and lecrolink user."""
    active = League.objects.filter(tier=1, active=True)
    if active.count() != 1:
        raise WorldBuildRefused('Exactly one active tier-1 league is required.')
    league = active.get()
    if league.slug != LEAGUE_SLUG or league.name != LEAGUE_NAME:
        raise WorldBuildRefused('The active tier-1 league is not Federation.')
    if League.objects.filter(name=LEAGUE_NAME, tier=1).count() != 1:
        raise WorldBuildRefused('More than one tier-1 Federation league exists.')
    if UniverseRelease.objects.count() != 1:
        raise WorldBuildRefused('Exactly one universe release is required.')
    release = UniverseRelease.objects.filter(root_league=league).first()
    if release is None:
        raise WorldBuildRefused('Exactly one universe release is required.')
    if (
        release.slug != RELEASE_SLUG
        or release.seed != 1970
        or release.founding_franchise_count != 26
        or release.expansion_schedule_version != 3
        or release.first_season_year != 1970
        or release.last_season_year != 2029
        or release.season_count != 60
    ):
        raise WorldBuildRefused('The release is not fg-1970 for 1970 through 2029.')
    user_model = get_user_model()
    if user_model.objects.filter(username=USERNAME).count() != 1:
        raise WorldBuildRefused('Exactly one lecrolink user is required.')
    supers = user_model.objects.filter(is_active=True, is_superuser=True)
    if supers.count() != 1:
        raise WorldBuildRefused('Exactly one active superuser is required.')
    user = supers.get()
    if user.username != USERNAME:
        raise WorldBuildRefused('The active superuser is not lecrolink.')
    return league, release, user


def inspect_world(release, user):
    snapshot = product_snapshot(release)
    template = template_status()
    timeline_count, timeline_exact, fixtures = timeline_facts(release, user)
    classification = classify_world(
        snapshot,
        status=release.status,
        generator_version=release.generator_version,
        fingerprint_kind=fingerprint_kind(release),
        published=release.published_at is not None,
        template=template,
        timeline_count=timeline_count,
        timeline_exact=timeline_exact,
        fixtures=fixtures,
    )
    return {
        'snapshot': snapshot,
        'template': template,
        'fixtures': fixtures,
        'timeline_count': timeline_count,
        'timeline_exact': timeline_exact,
        'classification': classification,
    }


def fingerprint_kind(release):
    catalog = release.catalog_fingerprint
    validation = release.validation_fingerprint
    if catalog == '' and validation == '':
        return 'blank'
    if HEX_DIGEST.fullmatch(catalog) and HEX_DIGEST.fullmatch(validation):
        return 'stored'
    return 'other'


def template_status():
    """Classify the stored 1970 template as ABSENT, EXACT, or DIFFERENT."""
    state = _stored_state(_checked_template())
    return {'absent': 'ABSENT', 'match': 'EXACT', 'differ': 'DIFFERENT'}[state]


def product_snapshot(release):
    """Read the product counts used to classify and refuse a mismatched set."""
    season_sizes = {year: 0 for year in SEASON_SIZES}
    season_rows = (
        FranchiseSeasonMembership.objects.filter(
            franchise__release=release,
            season_year__in=SEASON_SIZES,
        )
        .values('season_year')
        .annotate(total=Count('id'))
    )
    for row in season_rows:
        season_sizes[row['season_year']] = row['total']
    opening_players = release.players.filter(cohort=UniversePlayer.Cohort.OPENING).count()
    prospects = release.players.filter(cohort=UniversePlayer.Cohort.DRAFT).count()
    snapshot = {
        'franchises': release.franchises.count(),
        'memberships': FranchiseSeasonMembership.objects.filter(
            franchise__release=release,
        ).count(),
        'replaced_names': release.provisional_franchise_names.filter(
            city_status=ProvisionalFranchiseName.Status.REPLACED,
            nickname_status=ProvisionalFranchiseName.Status.REPLACED,
        ).count(),
        'expansion_events': release.expansion_events.count(),
        'expansion_clubs': ExpansionClub.objects.filter(event__release=release).count(),
        'staff_identities': release.staff_identities.count(),
        'general_managers': release.gms.count(),
        'head_coaches': release.coaches.count(),
        'seated_general_managers': release.opening_staff.filter(role='gm').count(),
        'seated_head_coaches': release.opening_staff.filter(role='head_coach').count(),
        'colleges': release.colleges.count(),
        'era_profiles': release.era_profiles.count(),
        'draft_classes': release.draft_classes.count(),
        'opening_players': opening_players,
        'opening_assignments': release.opening_assignments.count(),
        'starters': release.opening_assignments.filter(role='starter').count(),
        'backups': release.opening_assignments.filter(role='backup').count(),
        'taxi': release.opening_assignments.filter(role='taxi').count(),
        'opening_free_agents': release.opening_assignments.filter(role='free_agent').count(),
        'prospects': prospects,
        'draftable_prospects': release.players.filter(
            cohort=UniversePlayer.Cohort.DRAFT,
            draft_pool=UniversePlayer.DraftPool.DRAFTABLE,
        ).count(),
        'undrafted_prospects': release.players.filter(
            cohort=UniversePlayer.Cohort.DRAFT,
            draft_pool=UniversePlayer.DraftPool.UNDRAFTED,
        ).count(),
        'players': release.players.count(),
        'rating_profiles': PlayerRatingProfile.objects.filter(player__release=release).count(),
        'season_sizes': season_sizes,
        'has_y1970': release.era_profiles.filter(profile_key='y1970').exists(),
        'names_exact': False,
        'alignments_exact': False,
    }
    if snapshot['franchises'] == EXPECTED['franchises']:
        snapshot['names_exact'] = names_are_exact(release)
    if snapshot['memberships'] == EXPECTED['memberships']:
        snapshot['alignments_exact'] = alignments_are_exact(release)
    return snapshot


def names_are_exact(release):
    """Compare every fg-founding-2 name to APPROVED_FRANCHISE_NAMES.

    Counts are not enough. City, nickname, both replaced statuses, the
    franchise display name, and an empty catalog team must all match the
    canonical map for that placeholder slug.
    """
    validate_approved_names(APPROVED_FRANCHISE_NAMES)
    expected = {
        slug: (city, nickname)
        for slug, city, nickname in APPROVED_FRANCHISE_NAMES
    }
    stored = {
        row.placeholder_slug: row
        for row in release.provisional_franchise_names.all()
    }
    franchises = {
        row.placeholder_slug: row
        for row in release.franchises.all()
    }
    if (
        set(stored) != set(expected)
        or set(franchises) != set(expected)
        or len(stored) != len(expected)
        or len(franchises) != len(expected)
    ):
        return False
    for slug, (city, nickname) in expected.items():
        row = stored[slug]
        franchise = franchises[slug]
        if (
            row.city != city
            or row.nickname != nickname
            or row.city_status != REPLACED
            or row.nickname_status != REPLACED
            or franchise.display_name != display_name(city, nickname)
            or franchise.catalog_team_id is not None
        ):
            return False
    return True


def alignments_are_exact(release):
    """Compare every membership to the canonical fg-founding-2 schedule plan.

    The plan is ``membership_rows()``. A matching headcount per season is
    not enough. Each row must carry the plan's slug, season, conference,
    division, and slot, plus the canonical debut, origin, and selectable flag.
    """
    stored = list(
        FranchiseSeasonMembership.objects.filter(
            franchise__release=release,
        ).values_list(
            'franchise__placeholder_slug',
            'season_year',
            'conference_slug',
            'division_slug',
            'slot_order',
            'owner_selectable',
            'franchise__first_active_season',
            'franchise__final_active_season',
            'franchise__origin',
            'franchise__history_predecessor_id',
        )
    )
    expected = []
    for row in membership_rows():
        debut = DEBUT_YEAR[row['slug']]
        origin = (
            UniverseFranchise.Origin.FOUNDING
            if debut == 1970
            else UniverseFranchise.Origin.EXPANSION
        )
        expected.append((
            row['slug'],
            row['season_year'],
            row['conference_slug'],
            row['division_slug'],
            row['slot_order'],
            True,
            debut,
            None,
            origin,
            None,
        ))
    if len(stored) != len(expected) or len(expected) != EXPECTED['memberships']:
        return False
    stored_rows = {
        (*row[:5], bool(row[5]), *row[6:])
        for row in stored
    }
    return stored_rows == set(expected) and len(stored_rows) == len(expected)


def timeline_facts(release, user):
    timelines = list(Timeline.objects.filter(release=release))
    if not timelines:
        return 0, False, 'ABSENT'
    if len(timelines) > 1:
        return len(timelines), False, 'DIFFERENT'
    timeline = timelines[0]
    return 1, timeline_is_exact(timeline, user), fixture_status(timeline)


def timeline_is_exact(timeline, user):
    if (
        timeline.name != CAMPAIGN_NAME
        or timeline.created_by_id != user.pk
        or timeline.status != Timeline.Status.ACTIVE
        or timeline.start_season_year != 1970
        or timeline.current_season_year != 1970
        or timeline.phase != Timeline.Phase.DIRECTION
    ):
        return False
    counts = timeline_counts(timeline)
    if any(counts[key] != TIMELINE_COUNTS[key] for key in TIMELINE_COUNTS):
        return False
    return (
        timeline.participants.filter(user=user, status='active').count() == 1
        and timeline.franchise_controls.filter(
            franchise__placeholder_slug=FRANCHISE_SLUG,
        ).count() == 1
        and timeline.seasons.filter(season_year=1970).count() == 1
    )


def timeline_counts(timeline):
    memberships = RosterMembership.objects.filter(overlay__timeline=timeline)
    return {
        'participants': timeline.participants.count(),
        'controls': timeline.franchise_controls.count(),
        'seasons': timeline.seasons.count(),
        'directives': timeline.directives.count(),
        'timeline_players': timeline.players.count(),
        'season_ratings': PlayerSeasonRating.objects.filter(overlay__timeline=timeline).count(),
        'roster_memberships': memberships.count(),
        'rostered': memberships.filter(franchise__isnull=False).count(),
        'free_agents': memberships.filter(franchise__isnull=True).count(),
    }


def fixture_status(timeline):
    """Read the writer's assignment, game, and week comparison.

    Twenty-six assignments, 182 games, 182 weeks, and zero byes do not make
    a fixture set EXACT.
    """
    return historical_fixture_state(timeline)


def fixture_counts(timeline):
    return {
        'slot_assignments': TimelineScheduleSlotAssignment.objects.filter(
            timeline=timeline,
            season_year=1970,
        ).count(),
        'games': ScheduleGame.objects.filter(
            timeline=timeline,
            season_year=1970,
            kind=ScheduleGame.Kind.REGULAR,
        ).count(),
        'weeks': ScheduleWeekAssignment.objects.filter(
            timeline=timeline,
            season_year=1970,
        ).count(),
        'byes': ScheduleBye.objects.filter(
            timeline=timeline,
            season_year=1970,
        ).count(),
    }


def classify_world(
    snapshot,
    *,
    status,
    generator_version,
    fingerprint_kind,
    published,
    template,
    timeline_count,
    timeline_exact,
    fixtures,
):
    """Classify one inspected world. This function does not read the database."""
    prefix = product_prefix(snapshot)
    draft = (
        status == UniverseRelease.Status.DRAFT
        and fingerprint_kind == 'blank'
        and not published
    )
    published_exact = (
        status == UniverseRelease.Status.PUBLISHED
        and generator_version == GENERATOR_VERSION
        and fingerprint_kind == 'stored'
        and published
        and prefix == 'complete'
    )
    if timeline_count > 1 or (timeline_count == 1 and not timeline_exact):
        return 'INVALID'
    if timeline_count and status != UniverseRelease.Status.PUBLISHED:
        return 'INVALID'
    if fixtures == 'DIFFERENT' or (fixtures != 'ABSENT' and timeline_count == 0):
        return 'INVALID'
    if (
        draft
        and generator_version == 'pending'
        and prefix == 'empty'
        and timeline_count == 0
    ):
        return 'EMPTY_DRAFT'
    if (
        draft
        and generator_version == GENERATOR_VERSION
        and prefix == 'partial'
        and timeline_count == 0
    ):
        return 'PARTIAL'
    if (
        draft
        and generator_version == GENERATOR_VERSION
        and prefix == 'complete'
        and timeline_count == 0
    ):
        return 'BUILT_DRAFT'
    if published_exact and timeline_count == 0 and fixtures == 'ABSENT':
        return 'PUBLISHED'
    if (
        published_exact
        and timeline_count == 1
        and timeline_exact
        and fixtures == 'ABSENT'
        and template in ('ABSENT', 'EXACT')
    ):
        return 'PARTIAL'
    if (
        published_exact
        and timeline_count == 1
        and timeline_exact
        and fixtures == 'EXACT'
        and template == 'EXACT'
    ):
        return 'STARTED_1970'
    return 'INVALID'


def product_prefix(snapshot):
    prospects = _prospects_state(snapshot)
    layers = (
        _schedule_state(snapshot),
        _opening_state(snapshot, prospects),
        prospects,
        _ratings_state(snapshot),
    )
    if any(layer == 'bad' for layer in layers):
        return 'broken'
    seen_absent = False
    exact = 0
    for layer in layers:
        if layer == 'exact':
            if seen_absent:
                return 'broken'
            exact += 1
        elif layer == 'absent':
            seen_absent = True
        else:
            return 'broken'
    if exact == 0:
        return 'empty'
    if exact == len(layers):
        return 'complete'
    return 'partial'


def _schedule_state(snapshot):
    seasons_clear = all(snapshot['season_sizes'].get(year, 0) == 0 for year in SEASON_SIZES)
    if all(snapshot[key] == 0 for key in SCHEDULE_KEYS) and seasons_clear:
        return 'absent'
    seasons_exact = all(
        snapshot['season_sizes'].get(year) == count
        for year, count in SEASON_SIZES.items()
    )
    if (
        all(snapshot[key] == EXPECTED[key] for key in SCHEDULE_KEYS)
        and seasons_exact
        and snapshot['names_exact']
        and snapshot['alignments_exact']
    ):
        return 'exact'
    return 'bad'


def _opening_state(snapshot, prospects):
    clear = all(snapshot[key] == 0 for key in OPENING_KEYS)
    if clear and snapshot['era_profiles'] == 0 and not snapshot['has_y1970']:
        return 'absent'
    exact_counts = all(snapshot[key] == EXPECTED[key] for key in OPENING_KEYS)
    opening_only = (
        prospects == 'absent'
        and snapshot['era_profiles'] == 1
        and snapshot['draft_classes'] == 0
    )
    with_prospects = prospects == 'exact' and snapshot['era_profiles'] == EXPECTED['era_profiles']
    if exact_counts and snapshot['has_y1970'] and (opening_only or with_prospects):
        return 'exact'
    return 'bad'


def _prospects_state(snapshot):
    clear = all(snapshot[key] == 0 for key in PROSPECT_KEYS)
    if clear and snapshot['era_profiles'] in (0, 1):
        return 'absent'
    if (
        all(snapshot[key] == EXPECTED[key] for key in PROSPECT_KEYS)
        and snapshot['era_profiles'] == EXPECTED['era_profiles']
        and snapshot['has_y1970']
    ):
        return 'exact'
    return 'bad'


def _ratings_state(snapshot):
    cohort = snapshot['opening_players'] + snapshot['prospects']
    if snapshot['rating_profiles'] == 0 and snapshot['players'] == cohort:
        return 'absent'
    if (
        snapshot['rating_profiles'] == EXPECTED['rating_profiles']
        and snapshot['players'] == EXPECTED['players']
    ):
        return 'exact'
    return 'bad'


def write_counts(stdout, snapshot):
    for key in EXPECTED:
        stdout.write(f'count.{key}={snapshot[key]}')
    for year in SEASON_SIZES:
        stdout.write(f'count.season.{year}={snapshot["season_sizes"].get(year, 0)}')


def _record(stdout, name, outcome, accepted=WRITER_RESULTS):
    if outcome not in accepted:
        raise WorldBuildRefused(f'The {name} writer returned an unknown outcome.')
    stdout.write(f'writer={name} outcome={outcome}')
    return outcome


def assert_canonical_catalog(release):
    """Refuse stored names or seats that are not the canonical fg-founding-2 plan.

    An empty catalog has nothing to compare. Any stored franchise or
    membership is compared in full before a writing stage can change it.
    """
    franchise_count = release.franchises.count()
    membership_count = FranchiseSeasonMembership.objects.filter(
        franchise__release=release,
    ).count()
    if franchise_count == 0 and membership_count == 0:
        return
    if not names_are_exact(release):
        raise WorldBuildRefused(
            'The approved franchise names do not match. They were left unchanged.'
        )
    if not alignments_are_exact(release):
        raise WorldBuildRefused(
            'The franchise-season alignments do not match. They were left unchanged.'
        )


def build_draft(stdout, league, release, view):
    assert_canonical_catalog(release)
    if release.status != UniverseRelease.Status.DRAFT:
        raise WorldBuildRefused('BUILD_DRAFT requires a draft release.')
    if view['timeline_count']:
        raise WorldBuildRefused('BUILD_DRAFT refuses a release that already has a timeline.')
    if fingerprint_kind(release) != 'blank':
        raise WorldBuildRefused(
            'Stored fingerprints are already set. They were left unchanged.'
        )
    if view['classification'] not in ('EMPTY_DRAFT', 'PARTIAL', 'BUILT_DRAFT'):
        raise WorldBuildRefused(
            'The stored draft does not match the fg-founding-2 world. '
            'It was left unchanged.'
        )
    outcomes = []
    outcomes.append(_record(
        stdout,
        'schedule',
        generate_schedule_release(league.pk, release.seed),
    ))
    release.refresh_from_db()
    if release.generator_version != GENERATOR_VERSION:
        raise WorldBuildRefused(
            'The schedule writer did not leave generator fg-founding-2.'
        )
    outcomes.append(_record(
        stdout,
        'opening',
        generate_opening_players(league.pk, release.seed),
    ))
    outcomes.append(_record(
        stdout,
        'prospects',
        generate_prospects(league.pk, release.seed),
    ))
    outcomes.append(_record(
        stdout,
        'ratings',
        generate_player_ratings(league.pk, release.seed),
    ))
    release.refresh_from_db()
    if fingerprint_kind(release) != 'blank':
        raise WorldBuildRefused('BUILD_DRAFT does not store fingerprints.')
    after = product_snapshot(release)
    if product_prefix(after) != 'complete':
        raise WorldBuildRefused('The draft product does not match the expected counts.')
    if not after['names_exact'] or not after['alignments_exact']:
        raise WorldBuildRefused('The approved names or alignments do not match.')
    catalog, validation = release_digests(release)
    report = evaluate_release_publication(
        release,
        candidate_fingerprints=(catalog, validation),
    )
    ready = 'pass' if report.publishable else 'fail'
    stdout.write(f'writer=publication_ready outcome={ready}')
    release.refresh_from_db()
    if fingerprint_kind(release) != 'blank':
        raise WorldBuildRefused('Candidate fingerprints were stored.')
    if not report.publishable:
        raise WorldBuildRefused('Publication readiness failed.')
    if any(outcome in WRITE_RESULTS for outcome in outcomes):
        return 'writes'
    return 'unchanged'


def publish_draft(stdout, league, release, view):
    assert_canonical_catalog(release)
    if view['timeline_count']:
        raise WorldBuildRefused('PUBLISH refuses a release that already has a timeline.')
    if view['classification'] not in ('BUILT_DRAFT', 'PUBLISHED'):
        raise WorldBuildRefused(
            'PUBLISH requires an exact built draft or an exact published release.'
        )
    before = view['snapshot']
    was_draft = release.status == UniverseRelease.Status.DRAFT
    result = publish_universe_release(
        slug=RELEASE_SLUG,
        seed=1970,
        generator_version=GENERATOR_VERSION,
        league_id=league.pk,
    )
    _record(stdout, 'publish', result.outcome, ('published', 'unchanged'))
    if was_draft and result.outcome != 'published':
        raise WorldBuildRefused('The first publication did not publish the release.')
    if not was_draft and result.outcome != 'unchanged':
        raise WorldBuildRefused('The published release was written again.')
    release.refresh_from_db()
    after = product_snapshot(release)
    if not same_counts(before, after):
        raise WorldBuildRefused('Publication changed the world counts.')
    _require_published_release(release)
    if result.outcome == 'published':
        return 'writes'
    return 'unchanged'


def _require_published_release(release):
    if release.status != UniverseRelease.Status.PUBLISHED or release.published_at is None:
        raise WorldBuildRefused('The release is not published.')
    if fingerprint_kind(release) != 'stored':
        raise WorldBuildRefused('The stored fingerprints are not 64-character digests.')
    fresh = release_digests(release)
    stored = (release.catalog_fingerprint, release.validation_fingerprint)
    if fresh != stored:
        raise WorldBuildRefused('The stored fingerprints do not match a fresh computation.')


def same_counts(before, after):
    if any(before[key] != after[key] for key in EXPECTED):
        return False
    return before['season_sizes'] == after['season_sizes']


def start_1970(stdout, release, user, view):
    assert_canonical_catalog(release)
    if view['fixtures'] == 'DIFFERENT':
        raise WorldBuildRefused('The stored 1970 fixtures differ. They were left unchanged.')
    if view['template'] == 'DIFFERENT':
        raise WorldBuildRefused(
            'The stored 1970 historical template differs. It was left unchanged.'
        )
    if view['timeline_count'] > 1 or (view['timeline_count'] == 1 and not view['timeline_exact']):
        raise WorldBuildRefused('A different timeline exists. It was left unchanged.')
    if release.status != UniverseRelease.Status.PUBLISHED or view['classification'] not in (
        'PUBLISHED',
        'PARTIAL',
        'STARTED_1970',
    ):
        raise WorldBuildRefused('The release is not an exact published world.')
    template_outcome = 'unchanged'
    if view['template'] == 'ABSENT':
        template_outcome = load_historical_schedule_1970()
        _record(stdout, 'template', template_outcome)
        if template_status() != 'EXACT':
            raise WorldBuildRefused('The 1970 template did not match after loading.')
    elif view['template'] == 'EXACT':
        stdout.write('writer=template outcome=unchanged')
    else:
        raise WorldBuildRefused(
            'The stored 1970 historical template differs. It was left unchanged.'
        )
    started = start_timeline(
        slug=RELEASE_SLUG,
        seed=1970,
        generator_version=GENERATOR_VERSION,
        user_id=user.pk,
        name=CAMPAIGN_NAME,
        franchise_slug=FRANCHISE_SLUG,
    )
    _record(stdout, 'timeline', started.outcome)
    _require_start_counts(started)
    timeline = Timeline.objects.get(pk=started.timeline_id)
    if (
        timeline.release_id != release.pk
        or timeline.name != CAMPAIGN_NAME
        or timeline.created_by_id != user.pk
    ):
        raise WorldBuildRefused('The resolved timeline is not Opening 1970.')
    if fixture_status(timeline) == 'DIFFERENT':
        raise WorldBuildRefused('The stored 1970 fixtures differ. They were left unchanged.')
    fixture_outcome = generate_historical_timeline_schedule_1970(timeline_id=timeline.pk)
    _record(stdout, 'fixtures', fixture_outcome)
    if fixture_status(timeline) != 'EXACT':
        raise WorldBuildRefused('The 1970 fixtures do not match the historical plan.')
    if template_outcome == 'created' or started.outcome == 'created' or fixture_outcome == 'created':
        return 'writes'
    return 'unchanged'


def _require_start_counts(result):
    expected = {
        'player_count': TIMELINE_COUNTS['timeline_players'],
        'rating_count': TIMELINE_COUNTS['season_ratings'],
        'membership_count': TIMELINE_COUNTS['roster_memberships'],
        'rostered_count': TIMELINE_COUNTS['rostered'],
        'free_agent_count': TIMELINE_COUNTS['free_agents'],
    }
    for name, count in expected.items():
        if getattr(result, name) != count:
            raise WorldBuildRefused(f'The timeline {name} is not {count}.')


def empty_snapshot():
    """A product snapshot with every approved count at zero."""
    return {
        **{key: 0 for key in EXPECTED},
        'season_sizes': {year: 0 for year in SEASON_SIZES},
        'has_y1970': False,
        'names_exact': False,
        'alignments_exact': False,
    }


def exact_snapshot():
    """The approved built-world counts, names, and alignments."""
    return {
        **EXPECTED,
        'season_sizes': dict(SEASON_SIZES),
        'has_y1970': True,
        'names_exact': True,
        'alignments_exact': True,
    }
