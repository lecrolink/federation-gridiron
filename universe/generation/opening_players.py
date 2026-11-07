"""Write the 1,378 opening players onto an existing fg-founding-2 draft.

The release must already have its schedule. This module reuses that release,
its seed, and its founding franchises. It writes the 64 approved colleges,
the y1970 era profile, the opening players, and their opening assignments.
An identical rerun changes nothing. A different seed or a different stored
row is refused, and nothing is deleted.

Prospects, rating profiles, college weights, opening contracts, timelines,
stats, awards, standings, archive players, and simulation data are not
written. The founding generators are not called and are not modified.
"""

from django.core.exceptions import ValidationError
from django.db import DEFAULT_DB_ALIAS, IntegrityError

from timelines.models import Timeline
from universe.generation.founding import (
    FoundingGenerationError,
    _resolve_release_id,
    _validate_seed,
)
from universe.release_guard import (
    assert_release_lock_held,
    database_alias,
    holding_release_lock,
    parent_release_id,
)
from universe.generation.player_counts import (
    FIRST_SEASON_YEAR,
    LAST_SEASON_YEAR,
    SEASON_COUNT,
    active_quota,
    opening_cohort_count,
    quota_total,
    taxi_quota,
)
from universe.generation.player_identities import (
    build_opening_cohort,
    founding_club_slugs,
    growth_draws,
)
from universe.generation.player_names import COLLEGES
from universe.generation.schedule import FOUNDING_FRANCHISE_COUNT, GENERATOR_VERSION
from universe.models import UniverseFranchise, UniverseRelease
from universe.people import College, UniversePlayer
from universe.rules import EraRuleProfile, OpeningAssignment


PROFILE_KEY = 'y1970'
OPENING_LAST_YEAR = 1973
PLAYER_BATCH_SIZE = 500
# Foreign keys whose clean_fields() path would query once per opening player.
# Scalar fields stay in full_clean. Inserts still enforce the keys.
_OPENING_RELATION_FIELDS = frozenset((
    'release',
    'college',
    'origin_franchise',
    'draft_class',
    'origin_profile',
))
_ASSIGNMENT_RELATION_FIELDS = frozenset((
    'release',
    'player',
    'franchise',
))

_PLAYER_FIELDS = frozenset((
    'release',
    'college',
    'origin_franchise',
    'draft_class',
    'origin_profile',
    'universe_key',
    'name',
    'birth_year',
    'birth_day',
    'position',
    'secondary_position_1',
    'secondary_position_2',
    'handedness',
    'height_in',
    'weight_lb',
    'cohort',
    'draft_pool',
    'first_pro_year',
    'origin_draft_year',
    'origin_draft_round',
    'origin_draft_pick',
    'peak_age',
    'development_rate',
    'decline_rate',
    'board_rank',
    'trait_1',
    'trait_2',
    'trait_3',
))

_ASSIGNMENT_FIELDS = frozenset((
    'release',
    'player',
    'franchise',
    'role',
    'depth',
))

_COLLEGE_FIELDS = frozenset(('release', 'slug', 'name'))


class OpeningGenerationError(Exception):
    """The opening cohort cannot be written."""


def y1970_values():
    """The approved 1970–1973 controlled-market card, including quotas."""
    active = active_quota(FIRST_SEASON_YEAR)
    taxi = taxi_quota(FIRST_SEASON_YEAR)
    if quota_total(active) != 40 or quota_total(taxi) != 8:
        raise OpeningGenerationError('The 1970 roster quotas drifted.')
    values = {
        'profile_key': PROFILE_KEY,
        'first_year': FIRST_SEASON_YEAR,
        'last_year': OPENING_LAST_YEAR,
        'market': 'controlled',
        'style_key': 'run-first',
        'active_limit': 40,
        'taxi_limit': 8,
        'ir_max': 2,
        'ir_min_weeks': 8,
        'ir_returns_allowed': 0,
        'draft_rounds': 17,
        'rookie_contract_years_min': 1,
        'rookie_contract_years_max': 3,
        'veteran_contract_years_min': 1,
        'veteran_contract_years_max': 5,
        'accrued_seasons_for_ufa': None,
        'trade_deadline_week': 8,
        'future_pick_years': 1,
        'waiver_rule': 'all_releases',
        'free_agency_type': 'reserve',
        'fa_compensation': 'none',
        'cap_status': 'off',
        'cap_limit': None,
        'minimum_salary': None,
        'franchise_tag': 'off',
        'playoff_format': 'eight_team_3_and_1',
        'schedule_length': 14,
        'overtime_rule': 'tie',
        'sack_leaderboard': 'unofficial',
        'rookie_slot_scale': None,
    }
    for position, count in active.items():
        values[f'quota_active_{position.lower()}'] = count
    for position, count in taxi.items():
        values[f'quota_taxi_{position.lower()}'] = count
    return values


def generate_opening_players(league_id, seed):
    """Write the opening cohort. Return ``created`` or ``unchanged``."""
    try:
        _validate_seed(seed)
        release_id = _resolve_release_id(league_id)
    except FoundingGenerationError as exc:
        raise OpeningGenerationError(str(exc)) from exc
    try:
        with holding_release_lock(release_id, using=DEFAULT_DB_ALIAS) as release:
            _refuse_closed(release)
            franchises = _require_schedule(release)
            if release.seed != seed:
                raise OpeningGenerationError(
                    'The opening writer will not change the release seed.'
                )
            _require_known_fields()
            state = _opening_state(release, seed, franchises)
            if state == 'match':
                return 'unchanged'
            if state == 'differ' or _stored_rows_differ(release):
                raise OpeningGenerationError(
                    'Existing opening rows differ from this release. '
                    'The writer will not delete or replace them.'
                )
            _write_opening(release, seed, franchises)
            if _opening_state(release, seed, franchises) != 'match':
                raise OpeningGenerationError(
                    'The opening cohort did not match the approved builders.'
                )
            if (
                release.generator_version != GENERATOR_VERSION
                or release.seed != seed
                or release.status != UniverseRelease.Status.DRAFT
            ):
                raise OpeningGenerationError(
                    'The opening writer changed the release schedule metadata.'
                )
            return 'created'
    except IntegrityError as exc:
        raise OpeningGenerationError(
            'A generated key already exists outside this release.'
        ) from exc
    except (RuntimeError, ValueError) as exc:
        raise OpeningGenerationError(str(exc)) from exc


def _refuse_closed(release):
    if release.status != UniverseRelease.Status.DRAFT or release.published_at is not None:
        raise OpeningGenerationError('A published release cannot be changed.')
    if Timeline.objects.filter(release_id=release.pk).exists():
        raise OpeningGenerationError('A timeline points at this release.')


def _require_schedule(release):
    if release.generator_version != GENERATOR_VERSION:
        raise OpeningGenerationError(
            'The draft release must already have its fg-founding-2 schedule.'
        )
    if (
        release.founding_franchise_count != FOUNDING_FRANCHISE_COUNT
        or release.first_season_year != FIRST_SEASON_YEAR
        or release.season_count != SEASON_COUNT
        or release.last_season_year != LAST_SEASON_YEAR
    ):
        raise OpeningGenerationError(
            'The draft release must use 26 founding franchises and the '
            '1970–2029 horizon.'
        )
    wanted = set(founding_club_slugs())
    stored = {
        franchise.placeholder_slug: franchise
        for franchise in release.franchises.filter(placeholder_slug__in=wanted)
    }
    if set(stored) != wanted or len(wanted) != FOUNDING_FRANCHISE_COUNT:
        raise OpeningGenerationError(
            'The fg-founding-2 schedule is missing a founding franchise.'
        )
    for franchise in stored.values():
        if (
            franchise.origin != UniverseFranchise.Origin.FOUNDING
            or franchise.first_active_season != FIRST_SEASON_YEAR
        ):
            raise OpeningGenerationError(
                'An opening roster uses a founding franchise.'
            )
    return stored


def _require_known_fields():
    missing = _uncovered_fields(College, _COLLEGE_FIELDS)
    missing.extend(_uncovered_fields(UniversePlayer, _PLAYER_FIELDS))
    missing.extend(_uncovered_fields(OpeningAssignment, _ASSIGNMENT_FIELDS))
    profile_fields = set(y1970_values())
    profile_fields.add('release')
    missing.extend(_uncovered_fields(EraRuleProfile, profile_fields))
    if missing:
        raise OpeningGenerationError(
            'A required model field has no approved value: ' + ', '.join(missing)
        )


def _uncovered_fields(model, covered):
    missing = []
    for field in model._meta.concrete_fields:
        if field.name == 'id' or field.name in covered:
            continue
        missing.append(f'{model.__name__}.{field.name}')
    return missing


def _stored_rows_differ(release):
    approved = {slug: name for name, slug, _kind in COLLEGES}
    for college in release.colleges.all():
        if approved.get(college.slug) != college.name:
            return True
    profile = release.era_profiles.filter(profile_key=PROFILE_KEY).first()
    if profile is not None and not _same_profile(profile, y1970_values()):
        return True
    return False


def _same_profile(profile, expected):
    for field in profile._meta.concrete_fields:
        if field.name in ('id', 'release'):
            continue
        if getattr(profile, field.attname) != expected[field.name]:
            return False
    return True


def _opening_players(release):
    """The opening writer's owned rows. Draft and archive cohorts stay out."""
    return UniversePlayer.objects.filter(
        release=release,
        cohort=UniversePlayer.Cohort.OPENING,
    )


def _opening_state(release, seed, franchises):
    players = list(
        _opening_players(release).select_related(
            'college',
            'origin_profile',
        )
    )
    assignments = list(
        OpeningAssignment.objects.filter(release=release).select_related('franchise')
    )
    if not players and not assignments:
        return 'absent'
    built_rows = build_opening_cohort(seed, release.slug)
    if (
        len(players) != opening_cohort_count()
        or len(assignments) != len(players)
        or len(built_rows) != len(players)
    ):
        return 'differ'
    profile = release.era_profiles.filter(profile_key=PROFILE_KEY).first()
    if profile is None or not _same_profile(profile, y1970_values()):
        return 'differ'
    colleges = {college.slug: college for college in release.colleges.all()}
    approved = {slug: name for name, slug, _kind in COLLEGES}
    if set(colleges) != set(approved):
        return 'differ'
    for slug, name in approved.items():
        if colleges[slug].name != name:
            return 'differ'
    by_key = {player.universe_key: player for player in players}
    assignment_by_player = {row.player_id: row for row in assignments}
    if len(by_key) != len(players) or len(assignment_by_player) != len(assignments):
        return 'differ'
    for built in built_rows:
        player = by_key.get(built.universe_key)
        if player is None or not _player_matches(player, built, seed, profile):
            return 'differ'
        assignment = assignment_by_player.get(player.pk)
        if assignment is None or not _assignment_matches(
            assignment, built, franchises, release,
        ):
            return 'differ'
    return 'match'


def _player_matches(player, built, seed, profile):
    peak_age, development_rate, decline_rate = growth_draws(seed, built.universe_key)
    return all((
        player.release_id == profile.release_id,
        player.college.slug == built.college_slug,
        player.college.name == built.college_name,
        player.origin_profile_id == profile.pk,
        player.universe_key == built.universe_key,
        player.name == built.name,
        player.birth_year == built.birth_year,
        player.birth_day == built.birth_day,
        player.position == built.position,
        player.handedness == built.handedness,
        player.height_in == built.height_in,
        player.weight_lb == built.weight_lb,
        player.cohort == UniversePlayer.Cohort.OPENING,
        player.first_pro_year == built.debut_year,
        player.peak_age == peak_age,
        player.development_rate == development_rate,
        player.decline_rate == decline_rate,
        player.origin_franchise_id is None,
        player.draft_class_id is None,
        player.draft_pool is None,
        player.origin_draft_year is None,
        player.origin_draft_round is None,
        player.origin_draft_pick is None,
        player.board_rank is None,
        player.secondary_position_1 is None,
        player.secondary_position_2 is None,
        player.trait_1 is None,
        player.trait_2 is None,
        player.trait_3 is None,
    ))


def _assignment_matches(assignment, built, franchises, release):
    if assignment.release_id != release.pk or assignment.role != built.slot_kind:
        return False
    if not built.on_roster:
        return assignment.franchise_id is None and assignment.depth is None
    franchise = franchises[built.club_slug]
    return assignment.franchise_id == franchise.pk and assignment.depth == built.depth


def _write_opening(release, seed, franchises):
    approved = [(name, slug) for name, slug, _kind in COLLEGES]
    stored = {college.slug: college for college in release.colleges.all()}
    _save_all([
        College(release=release, slug=slug, name=name)
        for name, slug in approved
        if slug not in stored
    ])
    profile = release.era_profiles.filter(profile_key=PROFILE_KEY).first()
    if profile is None:
        _save_all([EraRuleProfile(release=release, **y1970_values())])
        profile = release.era_profiles.get(profile_key=PROFILE_KEY)
    colleges = {college.slug: college for college in release.colleges.all()}
    _require_opening_relation_ids(release, colleges, profile)
    built_rows = build_opening_cohort(seed, release.slug)
    if len(built_rows) != opening_cohort_count():
        raise OpeningGenerationError('The opening cohort drifted from 1,378.')
    players = []
    seen_keys = set()
    for built in built_rows:
        if built.universe_key in seen_keys:
            raise OpeningGenerationError('Opening keys must be unique.')
        seen_keys.add(built.universe_key)
        peak_age, development_rate, decline_rate = growth_draws(seed, built.universe_key)
        player = UniversePlayer(
            release=release,
            college=colleges[built.college_slug],
            origin_franchise=None,
            draft_class=None,
            origin_profile=profile,
            universe_key=built.universe_key,
            name=built.name,
            birth_year=built.birth_year,
            birth_day=built.birth_day,
            position=built.position,
            secondary_position_1=None,
            secondary_position_2=None,
            handedness=built.handedness,
            height_in=built.height_in,
            weight_lb=built.weight_lb,
            cohort=UniversePlayer.Cohort.OPENING,
            draft_pool=None,
            first_pro_year=built.debut_year,
            origin_draft_year=None,
            origin_draft_round=None,
            origin_draft_pick=None,
            peak_age=peak_age,
            development_rate=development_rate,
            decline_rate=decline_rate,
            board_rank=None,
            trait_1=None,
            trait_2=None,
            trait_3=None,
        )
        _require_trusted_opening_player(player, built, release, colleges, profile)
        players.append(player)
    _insert_opening_players(players)
    stored_players = {
        player.universe_key: player
        for player in _opening_players(release)
    }
    if set(stored_players) != seen_keys:
        raise OpeningGenerationError('The opening insert did not store the expected keys.')
    assignments = []
    for built in built_rows:
        player = stored_players[built.universe_key]
        assignment = OpeningAssignment(
            release=release,
            player=player,
            franchise=franchises[built.club_slug] if built.on_roster else None,
            role=built.slot_kind,
            depth=built.depth if built.on_roster else None,
        )
        _require_trusted_opening_assignment(
            assignment,
            built,
            release,
            player,
            franchises,
        )
        assignments.append(assignment)
    _save_assignments(assignments)


def _save_all(rows):
    """Ordinary full validation for the college and y1970 profile rows."""
    for row in rows:
        try:
            row.full_clean()
        except ValidationError as exc:
            raise OpeningGenerationError(
                f'A generated row failed validation: {exc}'
            ) from exc
    if rows:
        alias = database_alias(rows[0])
        release_id = parent_release_id(rows[0], using=alias)
        if release_id is None:
            raise OpeningGenerationError('A generated row has no parent release.')
        assert_release_lock_held(release_id, using=alias)
        type(rows[0]).objects.bulk_create(rows, batch_size=PLAYER_BATCH_SIZE)


def _cached_related(instance, name):
    """Return a related object already loaded on the instance. Never fetches."""
    return instance._state.fields_cache.get(name)


def _relation_exclude(model, expected):
    actual = frozenset(
        field.name
        for field in model._meta.concrete_fields
        if field.is_relation
    )
    if actual != expected:
        raise OpeningGenerationError(
            f'{model.__name__} foreign keys changed. Opening validation must name each one.'
        )
    return expected


def _require_opening_relation_ids(release, colleges, profile):
    """Require stored ids before any opening player is constructed."""
    if release.pk is None:
        raise OpeningGenerationError('The target release is not stored.')
    approved = {slug: name for name, slug, _kind in COLLEGES}
    if set(colleges) != set(approved) or len(colleges) != len(COLLEGES):
        raise OpeningGenerationError(
            'The opening writer expected the 64 approved colleges.'
        )
    seen_ids = set()
    for slug, college in colleges.items():
        if (
            college.pk is None
            or college.release_id != release.pk
            or college.name != approved[slug]
            or college.slug != slug
        ):
            raise OpeningGenerationError('An opening college is missing its stored id.')
        seen_ids.add(college.pk)
    if len(seen_ids) != len(COLLEGES):
        raise OpeningGenerationError(
            'The opening writer expected the 64 approved colleges.'
        )
    if (
        profile is None
        or profile.pk is None
        or profile.release_id != release.pk
        or profile.profile_key != PROFILE_KEY
        or profile.first_year != FIRST_SEASON_YEAR
        or profile.last_year != OPENING_LAST_YEAR
    ):
        raise OpeningGenerationError('The y1970 profile is missing its stored id.')


def _require_trusted_opening_player(player, built, release, colleges, profile):
    """Check excluded player foreign keys against objects already in memory."""
    cached_release = _cached_related(player, 'release')
    if (
        release.pk is None
        or player.release_id != release.pk
        or cached_release is None
        or cached_release.pk != release.pk
    ):
        raise OpeningGenerationError(
            'A generated opening player release is not the locked release.'
        )
    if (
        player.origin_franchise_id is not None
        or _cached_related(player, 'origin_franchise') is not None
    ):
        raise OpeningGenerationError(
            'A generated opening player origin franchise must be null.'
        )
    if player.draft_class_id is not None or _cached_related(player, 'draft_class') is not None:
        raise OpeningGenerationError(
            'A generated opening player draft class must be null.'
        )
    college = colleges.get(built.college_slug)
    cached_college = _cached_related(player, 'college')
    if (
        college is None
        or college.pk is None
        or cached_college is None
        or player.college_id != college.pk
        or cached_college.pk != college.pk
        or college.release_id != release.pk
        or cached_college.release_id != release.pk
        or college.slug != built.college_slug
        or college.name != built.college_name
    ):
        raise OpeningGenerationError(
            'A generated opening player college is not one of the stored colleges.'
        )
    cached_profile = _cached_related(player, 'origin_profile')
    if (
        profile is None
        or profile.pk is None
        or cached_profile is None
        or player.origin_profile_id != profile.pk
        or cached_profile.pk != profile.pk
        or profile.release_id != release.pk
        or profile.profile_key != PROFILE_KEY
        or not (profile.first_year <= FIRST_SEASON_YEAR <= profile.last_year)
    ):
        raise OpeningGenerationError(
            'A generated opening player origin profile is not the stored y1970 profile.'
        )


def _require_trusted_opening_assignment(assignment, built, release, player, franchises):
    """Check assignment release, player, and franchise without querying."""
    cached_release = _cached_related(assignment, 'release')
    cached_player = _cached_related(assignment, 'player')
    if (
        release.pk is None
        or assignment.release_id != release.pk
        or cached_release is None
        or cached_release.pk != release.pk
        or player.pk is None
        or player.release_id != release.pk
        or player.cohort != UniversePlayer.Cohort.OPENING
        or assignment.player_id != player.pk
        or cached_player is None
        or cached_player.pk != player.pk
        or assignment.role != built.slot_kind
    ):
        raise OpeningGenerationError(
            'A generated opening assignment does not match its player or release.'
        )
    cached_franchise = _cached_related(assignment, 'franchise')
    if not built.on_roster:
        if (
            assignment.franchise_id is not None
            or cached_franchise is not None
            or assignment.depth is not None
            or built.slot_kind != 'free_agent'
        ):
            raise OpeningGenerationError(
                'A generated opening free agent has a franchise or depth.'
            )
        return
    franchise = franchises[built.club_slug]
    if (
        franchise.pk is None
        or franchise.release_id != release.pk
        or franchise.origin != UniverseFranchise.Origin.FOUNDING
        or assignment.franchise_id != franchise.pk
        or cached_franchise is None
        or cached_franchise.pk != franchise.pk
        or cached_franchise.release_id != release.pk
        or assignment.depth != built.depth
        or assignment.depth is None
    ):
        raise OpeningGenerationError(
            'A generated opening assignment franchise is not the stored founding club.'
        )


def _validate_opening_players(rows):
    """Scalar clean_fields, clean, and extra_clean, without relation queries."""
    exclude = _relation_exclude(UniversePlayer, _OPENING_RELATION_FIELDS)
    for row in rows:
        try:
            row.full_clean(
                exclude=exclude,
                validate_unique=False,
                validate_constraints=False,
            )
        except ValidationError as exc:
            raise OpeningGenerationError(
                f'A generated row failed validation: {exc}'
            ) from exc


def _insert_opening_players(rows):
    _validate_opening_players(rows)
    alias = database_alias(rows[0])
    release_id = parent_release_id(rows[0], using=alias)
    if release_id is None:
        raise OpeningGenerationError('A generated row has no parent release.')
    assert_release_lock_held(release_id, using=alias)
    try:
        UniversePlayer.objects.bulk_create(rows, batch_size=PLAYER_BATCH_SIZE)
    except IntegrityError as exc:
        raise OpeningGenerationError(
            'An opening insert violated a database constraint. '
            'The writer rolled back the colleges, profile, players, and assignments.'
        ) from exc


def _save_assignments(rows):
    """Scalar assignment validation. Relationship checks already ran in memory."""
    exclude = _relation_exclude(OpeningAssignment, _ASSIGNMENT_RELATION_FIELDS)
    for row in rows:
        try:
            row.full_clean(
                exclude=exclude,
                validate_unique=False,
                validate_constraints=False,
            )
        except ValidationError as exc:
            raise OpeningGenerationError(
                f'A generated row failed validation: {exc}'
            ) from exc
    alias = database_alias(rows[0])
    release_id = parent_release_id(rows[0], using=alias)
    if release_id is None:
        raise OpeningGenerationError('A generated row has no parent release.')
    assert_release_lock_held(release_id, using=alias)
    try:
        OpeningAssignment.objects.bulk_create(rows, batch_size=PLAYER_BATCH_SIZE)
    except IntegrityError as exc:
        raise OpeningGenerationError(
            'An opening insert violated a database constraint. '
            'The writer rolled back the colleges, profile, players, and assignments.'
        ) from exc
