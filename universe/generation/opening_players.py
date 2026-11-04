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
from django.db import IntegrityError, transaction

from timelines.models import Timeline
from universe.generation.founding import (
    FoundingGenerationError,
    _resolve_release_id,
    _validate_seed,
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
    hmac_int,
)
from universe.generation.player_names import COLLEGES
from universe.generation.schedule import FOUNDING_FRANCHISE_COUNT, GENERATOR_VERSION
from universe.models import UniverseFranchise, UniverseRelease
from universe.people import College, UniversePlayer
from universe.rules import EraRuleProfile, OpeningAssignment


PROFILE_KEY = 'y1970'
OPENING_LAST_YEAR = 1973

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


def growth_draws(seed, universe_key):
    """Approved peak age and development rates for one stored player key.

    The decimal seed is the HMAC key. The message is the stored universe
    key plus the field name. Inclusive results are peak age 24–32 and both
    rates 25–75.
    """
    peak_age = 24 + (hmac_int(seed, f'{universe_key}:peak_age') % 9)
    development_rate = 25 + (
        hmac_int(seed, f'{universe_key}:development_rate') % 51
    )
    decline_rate = 25 + (hmac_int(seed, f'{universe_key}:decline_rate') % 51)
    return peak_age, development_rate, decline_rate


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
        with transaction.atomic():
            release = UniverseRelease.objects.select_for_update().get(pk=release_id)
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


def _opening_state(release, seed, franchises):
    players = list(
        UniversePlayer.objects.filter(release=release).select_related(
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
    built_rows = build_opening_cohort(seed, release.slug)
    players = []
    for built in built_rows:
        peak_age, development_rate, decline_rate = growth_draws(seed, built.universe_key)
        players.append(UniversePlayer(
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
        ))
    _save_all(players)
    stored_players = {
        player.universe_key: player
        for player in UniversePlayer.objects.filter(release=release)
    }
    assignments = []
    for built in built_rows:
        assignments.append(OpeningAssignment(
            release=release,
            player=stored_players[built.universe_key],
            franchise=franchises[built.club_slug] if built.on_roster else None,
            role=built.slot_kind,
            depth=built.depth if built.on_roster else None,
        ))
    _save_all(assignments)


def _save_all(rows):
    for row in rows:
        try:
            row.full_clean()
        except ValidationError as exc:
            raise OpeningGenerationError(
                f'A generated row failed validation: {exc}'
            ) from exc
    if rows:
        type(rows[0]).objects.bulk_create(rows, batch_size=500)
