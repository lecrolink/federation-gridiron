"""Write the draft-only prospect universe onto an existing fg-founding-2 draft.

The release must already have its schedule, the opening writer's 64 colleges,
the approved y1970 profile, and the 1,378 opening players and assignments.
This module adds the 11 later era profiles, the 60 draft classes, and the
19,777 prospect rows. It does not publish, and it does not change the
release seed, fingerprints, schedule version, founding rows, or opening rows.

Cap-off profiles store a null cap_limit. Cap-on profiles store the approved
flat cap of 10000 ledger units. minimum_salary and rookie_slot_scale stay
null. An identical rerun changes nothing. A stored cap that differs from
those values fails closed: the writer does not update or replace the row.
A different seed or a different stored row is refused, and nothing is deleted.
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
from universe.generation.opening_players import y1970_values
from universe.generation.player_counts import (
    FIRST_SEASON_YEAR,
    LAST_SEASON_YEAR,
    SEASON_COUNT,
    UNDRAFTED_MULTIPLIER,
    active_quota,
    draft_classes,
    draft_rounds,
    opening_cohort_count,
    prospect_totals,
    quota_total,
    taxi_quota,
)
from universe.generation.player_identities import (
    DRAFT_POOL_DRAFTABLE,
    DRAFT_POOL_UNDRAFTED,
    build_opening_cohort,
    build_prospects,
    founding_club_slugs,
    growth_draws,
)
from universe.generation.player_names import COLLEGES
from universe.generation.schedule import FOUNDING_FRANCHISE_COUNT, GENERATOR_VERSION
from universe.models import UniverseFranchise, UniverseRelease
from universe.people import UniversePlayer
from universe.rules import DraftClass, EraRuleProfile, OpeningAssignment


OPENING_PROFILE_KEY = 'y1970'
PLAYER_BATCH_SIZE = 500
# Foreign keys whose clean_fields() path would query the database.
# Scalar fields stay in full_clean. Inserts still enforce the keys.
_PROSPECT_RELATION_FIELDS = frozenset((
    'release',
    'college',
    'origin_franchise',
    'draft_class',
    'origin_profile',
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

_CLASS_FIELDS = frozenset((
    'release',
    'profile',
    'season_year',
    'franchise_count',
    'rounds',
    'draftable_count',
    'undrafted_multiplier',
    'undrafted_count',
))

_CONTROLLED_MOVEMENT = {
    'market': 'controlled',
    'ir_max': 2,
    'ir_min_weeks': 8,
    'ir_returns_allowed': 0,
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
    'franchise_tag': 'off',
    'cap_status': 'off',
    'sack_leaderboard': 'unofficial',
}

_TRANSITIONAL_MOVEMENT = {
    'market': 'transitional',
    'ir_max': 3,
    'ir_min_weeks': 4,
    'ir_returns_allowed': 1,
    'rookie_contract_years_min': 2,
    'rookie_contract_years_max': 3,
    'veteran_contract_years_min': 1,
    'veteran_contract_years_max': 5,
    'accrued_seasons_for_ufa': 4,
    'trade_deadline_week': 8,
    'future_pick_years': 2,
    'waiver_rule': 'veterans_until_deadline',
    'free_agency_type': 'restricted',
    'fa_compensation': 'tender_pick',
    'franchise_tag': 'off',
    'cap_status': 'off',
    'sack_leaderboard': 'official',
}

_MODERN_MOVEMENT = {
    'market': 'modern',
    'ir_max': 4,
    'ir_min_weeks': 4,
    'ir_returns_allowed': 2,
    'rookie_contract_years_min': 4,
    'rookie_contract_years_max': 4,
    'veteran_contract_years_min': 1,
    'veteran_contract_years_max': 5,
    'accrued_seasons_for_ufa': 4,
    'trade_deadline_week': 8,
    'future_pick_years': 3,
    'waiver_rule': 'veterans_until_cutdown',
    'free_agency_type': 'unrestricted_and_restricted',
    'fa_compensation': 'restricted_only',
    'franchise_tag': 'top_five_position_average',
    'cap_status': 'on',
    'sack_leaderboard': 'official',
}

# profile_key, first_year, last_year, style_key, active, taxi, rounds, games,
# playoff, overtime, movement.
_LATER_CARDS = (
    ('y1974', 1974, 1975, 'run-first', 40, 8, 17, 14, 'eight_team_3_and_1', 'sudden_death', _CONTROLLED_MOVEMENT),
    ('y1976', 1976, 1977, 'run-first', 40, 8, 17, 14, 'eight_team_2_and_2', 'sudden_death', _CONTROLLED_MOVEMENT),
    ('y1978', 1978, 1986, 'passing-rules', 45, 8, 12, 16, 'eight_team_2_and_2', 'sudden_death', _CONTROLLED_MOVEMENT),
    ('y1987', 1987, 1994, 'timing-specialists', 47, 8, 8, 16, 'eight_team_2_and_2', 'sudden_death', _TRANSITIONAL_MOVEMENT),
    ('y1995', 1995, 1998, 'cap-and-size', 53, 8, 7, 16, 'eight_team_3_and_1', 'sudden_death', _MODERN_MOVEMENT),
    ('y1999', 1999, 2001, 'cap-and-size', 53, 8, 7, 16, 'twelve_team_fill_to_six', 'sudden_death', _MODERN_MOVEMENT),
    ('y2002', 2002, 2003, 'cap-and-size', 53, 8, 7, 16, 'twelve_team_4_and_2', 'sudden_death', _MODERN_MOVEMENT),
    ('y2004', 2004, 2011, 'spread-influence', 53, 8, 7, 16, 'twelve_team_4_and_2', 'sudden_death', _MODERN_MOVEMENT),
    ('y2012', 2012, 2013, 'spread-influence', 53, 8, 7, 16, 'twelve_team_4_and_2', 'both_teams_possess', _MODERN_MOVEMENT),
    ('y2014', 2014, 2020, 'modern-passing', 53, 8, 7, 16, 'twelve_team_4_and_2', 'both_teams_possess', _MODERN_MOVEMENT),
    ('y2021', 2021, 2029, 'modern-passing', 53, 8, 7, 17, 'twelve_team_4_and_2', 'both_teams_possess', _MODERN_MOVEMENT),
)


class ProspectGenerationError(Exception):
    """The prospect cohort cannot be written."""


# Approved fictional ledger units for every cap-on profile. Not a dollar amount.
APPROVED_CAP_LIMIT = 10000


def later_profile_values():
    """The 11 approved cards from y1974 through y2021.

    Cap-off cards keep a null cap_limit. Cap-on cards store
    ``APPROVED_CAP_LIMIT``. minimum_salary and rookie_slot_scale stay null.
    """
    rows = []
    previous_last = 1973
    fill_to_six = 0
    for card in _LATER_CARDS:
        (
            profile_key,
            first_year,
            last_year,
            style_key,
            active_limit,
            taxi_limit,
            rounds,
            games,
            playoff_format,
            overtime_rule,
            movement,
        ) = card
        if first_year != previous_last + 1 or last_year < first_year:
            raise ProspectGenerationError('Era profile spans must not gap or overlap.')
        if draft_rounds(first_year) != rounds or draft_rounds(last_year) != rounds:
            raise ProspectGenerationError(f'{profile_key} draft rounds drifted.')
        if quota_total(active_quota(first_year)) != active_limit:
            raise ProspectGenerationError(f'{profile_key} active quota drifted.')
        if quota_total(active_quota(last_year)) != active_limit:
            raise ProspectGenerationError(f'{profile_key} active quota drifted.')
        if quota_total(taxi_quota(first_year)) != taxi_limit:
            raise ProspectGenerationError(f'{profile_key} taxi quota drifted.')
        if games < 8:
            raise ProspectGenerationError(f'{profile_key} cannot hold the trade deadline.')
        values = {
            'profile_key': profile_key,
            'first_year': first_year,
            'last_year': last_year,
            'style_key': style_key,
            'active_limit': active_limit,
            'taxi_limit': taxi_limit,
            'draft_rounds': rounds,
            'playoff_format': playoff_format,
            'schedule_length': games,
            'overtime_rule': overtime_rule,
            'minimum_salary': None,
            'rookie_slot_scale': None,
        }
        values.update(movement)
        if values['cap_status'] == 'on':
            values['cap_limit'] = APPROVED_CAP_LIMIT
        else:
            values['cap_limit'] = None
        for position, count in active_quota(first_year).items():
            values[f'quota_active_{position.lower()}'] = count
        for position, count in taxi_quota(first_year).items():
            values[f'quota_taxi_{position.lower()}'] = count
        if playoff_format == 'twelve_team_fill_to_six':
            fill_to_six += 1
        rows.append(values)
        previous_last = last_year
    if previous_last != LAST_SEASON_YEAR or len(rows) != 11 or fill_to_six != 1:
        raise ProspectGenerationError('The later era calendar drifted.')
    return tuple(rows)


def generate_prospects(league_id, seed):
    """Write the prospect cohort. Return ``created`` or ``unchanged``."""
    try:
        _validate_seed(seed)
        release_id = _resolve_release_id(league_id)
    except FoundingGenerationError as exc:
        raise ProspectGenerationError(str(exc)) from exc
    try:
        with holding_release_lock(release_id, using=DEFAULT_DB_ALIAS) as release:
            metadata = _release_metadata(release)
            _refuse_closed(release)
            _require_schedule(release)
            if release.seed != seed:
                raise ProspectGenerationError(
                    'The prospect writer will not change the release seed.'
                )
            _require_known_fields()
            if not _opening_matches(release, seed):
                raise ProspectGenerationError(
                    'The draft release must already have the opening writer\'s '
                    'colleges, y1970 profile, and opening cohort. The prospect '
                    'writer will not delete or replace them.'
                )
            state = _prospect_state(release, seed)
            if state == 'match':
                return 'unchanged'
            if state == 'differ':
                raise ProspectGenerationError(
                    'Existing prospect rows differ from this release. '
                    'The writer will not delete or replace them.'
                )
            _write_prospects(release, seed)
            if _prospect_state(release, seed) != 'match':
                raise ProspectGenerationError(
                    'The prospect cohort did not match the approved builders.'
                )
            release.refresh_from_db()
            if _release_metadata(release) != metadata:
                raise ProspectGenerationError(
                    'The prospect writer changed the release metadata.'
                )
            return 'created'
    except IntegrityError as exc:
        raise ProspectGenerationError(
            'A generated key already exists outside this release.'
        ) from exc
    except (RuntimeError, ValueError) as exc:
        raise ProspectGenerationError(str(exc)) from exc


def _release_metadata(release):
    return (
        release.generator_version,
        release.seed,
        release.status,
        release.catalog_fingerprint,
        release.validation_fingerprint,
        release.expansion_schedule_version,
        release.published_at,
        release.founding_franchise_count,
        release.first_season_year,
        release.season_count,
        release.last_season_year,
        release.slug,
    )


def _refuse_closed(release):
    if release.status != UniverseRelease.Status.DRAFT or release.published_at is not None:
        raise ProspectGenerationError('A published release cannot be changed.')
    if Timeline.objects.filter(release_id=release.pk).exists():
        raise ProspectGenerationError('A timeline points at this release.')


def _require_schedule(release):
    if release.generator_version != GENERATOR_VERSION:
        raise ProspectGenerationError(
            'The draft release must already have its fg-founding-2 schedule.'
        )
    if (
        release.founding_franchise_count != FOUNDING_FRANCHISE_COUNT
        or release.first_season_year != FIRST_SEASON_YEAR
        or release.season_count != SEASON_COUNT
        or release.last_season_year != LAST_SEASON_YEAR
    ):
        raise ProspectGenerationError(
            'The draft release must use 26 founding franchises and the '
            '1970–2029 horizon.'
        )
    wanted = set(founding_club_slugs())
    stored = {
        franchise.placeholder_slug: franchise
        for franchise in release.franchises.filter(placeholder_slug__in=wanted)
    }
    if set(stored) != wanted or len(wanted) != FOUNDING_FRANCHISE_COUNT:
        raise ProspectGenerationError(
            'The fg-founding-2 schedule is missing a founding franchise.'
        )
    for franchise in stored.values():
        if (
            franchise.origin != UniverseFranchise.Origin.FOUNDING
            or franchise.first_active_season != FIRST_SEASON_YEAR
        ):
            raise ProspectGenerationError(
                'The fg-founding-2 schedule is missing a founding franchise.'
            )


def _require_known_fields():
    missing = []
    profile_fields = set(later_profile_values()[0])
    profile_fields.add('release')
    missing.extend(_uncovered_fields(EraRuleProfile, profile_fields))
    missing.extend(_uncovered_fields(DraftClass, _CLASS_FIELDS))
    missing.extend(_uncovered_fields(UniversePlayer, _PLAYER_FIELDS))
    if missing:
        raise ProspectGenerationError(
            'A required model field has no approved value: ' + ', '.join(missing)
        )


def _uncovered_fields(model, covered):
    missing = []
    for field in model._meta.concrete_fields:
        if field.name == 'id' or field.name in covered:
            continue
        missing.append(f'{model.__name__}.{field.name}')
    return missing


def _same_profile(profile, expected):
    for field in profile._meta.concrete_fields:
        if field.name in ('id', 'release'):
            continue
        if getattr(profile, field.attname) != expected[field.name]:
            return False
    return True


def _opening_matches(release, seed):
    approved = {slug: name for name, slug, _kind in COLLEGES}
    colleges = {college.slug: college for college in release.colleges.all()}
    if set(colleges) != set(approved):
        return False
    for slug, name in approved.items():
        if colleges[slug].name != name:
            return False
    profile = release.era_profiles.filter(profile_key=OPENING_PROFILE_KEY).first()
    if profile is None or not _same_profile(profile, y1970_values()):
        return False
    players = list(
        UniversePlayer.objects.filter(
            release=release,
            cohort=UniversePlayer.Cohort.OPENING,
        ).select_related('college', 'origin_profile')
    )
    assignments = list(
        OpeningAssignment.objects.filter(
            release=release,
            player__cohort=UniversePlayer.Cohort.OPENING,
        ).select_related('franchise')
    )
    if (
        len(players) != opening_cohort_count()
        or len(assignments) != len(players)
        or OpeningAssignment.objects.filter(release=release).count() != len(players)
    ):
        return False
    built_rows = build_opening_cohort(seed, release.slug)
    if len(built_rows) != len(players):
        return False
    franchises = {
        franchise.placeholder_slug: franchise
        for franchise in release.franchises.filter(
            placeholder_slug__in=founding_club_slugs(),
        )
    }
    by_key = {player.universe_key: player for player in players}
    assignment_by_player = {row.player_id: row for row in assignments}
    if len(by_key) != len(players) or len(assignment_by_player) != len(assignments):
        return False
    for built in built_rows:
        player = by_key.get(built.universe_key)
        assignment = assignment_by_player.get(player.pk) if player is not None else None
        if (
            player is None
            or assignment is None
            or not _opening_player_matches(player, built, seed, profile)
            or not _opening_assignment_matches(assignment, built, franchises, release)
        ):
            return False
    return True


def _opening_player_matches(player, built, seed, profile):
    peak_age, development_rate, decline_rate = growth_draws(seed, built.universe_key)
    return all((
        player.release_id == profile.release_id,
        player.college.slug == built.college_slug,
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


def _opening_assignment_matches(assignment, built, franchises, release):
    if assignment.release_id != release.pk or assignment.role != built.slot_kind:
        return False
    if not built.on_roster:
        return assignment.franchise_id is None and assignment.depth is None
    franchise = franchises[built.club_slug]
    return assignment.franchise_id == franchise.pk and assignment.depth == built.depth


def _profile_for_year(profiles, year):
    for profile in profiles:
        if profile.first_year <= year <= profile.last_year:
            return profile
    return None


def _expected_pool(index, rounds):
    """Draftable seats are the first ``rounds`` offsets of each generation block."""
    if index % (rounds + UNDRAFTED_MULTIPLIER) < rounds:
        return DRAFT_POOL_DRAFTABLE
    return DRAFT_POOL_UNDRAFTED


def _prospect_years(built):
    """Class year on a draftable prospect. Undrafted prospects store neither year."""
    if built.draft_pool == DRAFT_POOL_DRAFTABLE:
        if built.debut_year != built.season_year:
            raise ProspectGenerationError(
                'A draftable prospect must debut in its class year.'
            )
        return built.season_year, built.season_year
    if built.debut_year is not None:
        raise ProspectGenerationError(
            'An undrafted prospect has no shared professional debut.'
        )
    return None, None


def _prospect_state(release, seed):
    expected_profiles = later_profile_values()
    stored_profiles = list(
        release.era_profiles.exclude(profile_key=OPENING_PROFILE_KEY).order_by('first_year')
    )
    stored_classes = list(release.draft_classes.select_related('profile').order_by('season_year'))
    draft_count = UniversePlayer.objects.filter(
        release=release,
        cohort=UniversePlayer.Cohort.DRAFT,
    ).count()
    if not stored_profiles and not stored_classes and draft_count == 0:
        return 'absent'
    draftable, undrafted, prospects = prospect_totals()
    if (
        len(stored_profiles) != len(expected_profiles)
        or len(stored_classes) != (LAST_SEASON_YEAR - FIRST_SEASON_YEAR + 1)
        or draft_count != prospects
    ):
        return 'differ'
    if release.era_profiles.count() != len(expected_profiles) + 1:
        return 'differ'
    for profile, expected in zip(stored_profiles, expected_profiles):
        if not _same_profile(profile, expected):
            return 'differ'
    profiles = list(release.era_profiles.order_by('first_year'))
    counts = draft_classes()
    if len(stored_classes) != len(counts):
        return 'differ'
    for stored, count in zip(stored_classes, counts):
        profile = _profile_for_year(profiles, count.season_year)
        if profile is None or not _class_matches(stored, count, profile):
            return 'differ'
    players = UniversePlayer.objects.filter(
        release=release,
        cohort=UniversePlayer.Cohort.DRAFT,
    ).select_related('college', 'origin_profile', 'draft_class')
    by_key = {player.universe_key: player for player in players}
    if len(by_key) != draft_count:
        return 'differ'
    built_rows = build_prospects(seed, release.slug)
    if len(built_rows) != draft_count:
        return 'differ'
    classes_by_year = {row.season_year: row for row in stored_classes}
    for built in built_rows:
        player = by_key.get(built.universe_key)
        draft_class = classes_by_year.get(built.season_year)
        profile = _profile_for_year(profiles, built.season_year)
        if (
            player is None
            or draft_class is None
            or profile is None
            or not _prospect_matches(player, built, draft_class, profile)
        ):
            return 'differ'
    if (
        UniversePlayer.objects.filter(
            release=release,
            cohort=UniversePlayer.Cohort.DRAFT,
            draft_pool=UniversePlayer.DraftPool.DRAFTABLE,
        ).count() != draftable
        or UniversePlayer.objects.filter(
            release=release,
            cohort=UniversePlayer.Cohort.DRAFT,
            draft_pool=UniversePlayer.DraftPool.UNDRAFTED,
        ).count() != undrafted
    ):
        return 'differ'
    return 'match'


def _class_matches(stored, count, profile):
    return all((
        stored.profile_id == profile.pk,
        stored.season_year == count.season_year,
        stored.franchise_count == count.franchise_count,
        stored.rounds == count.rounds,
        stored.rounds == profile.draft_rounds,
        stored.draftable_count == count.draftable_count,
        stored.undrafted_multiplier == UNDRAFTED_MULTIPLIER,
        stored.undrafted_count == count.undrafted_count,
        stored.draftable_count == stored.rounds * stored.franchise_count,
        stored.undrafted_count == stored.undrafted_multiplier * stored.franchise_count,
    ))


def _generated_prospect_matches(player, built, draft_class, profile):
    """In-memory class, pool, year, body, growth, and null-field checks.

    These run before insertion. They do not query the database. Check and
    unique constraints are left to the batched insert.
    """
    expected_pool = _expected_pool(built.index, draft_class.rounds)
    if built.draft_pool != expected_pool or player.draft_pool != expected_pool:
        return False
    if expected_pool == DRAFT_POOL_DRAFTABLE:
        years_match = (
            player.first_pro_year == built.season_year
            and player.origin_draft_year == built.season_year
        )
    else:
        years_match = (
            player.first_pro_year is None
            and player.origin_draft_year is None
        )
    return years_match and all((
        player.release_id == profile.release_id,
        player.college.slug == built.college_slug,
        player.college.name == built.college_name,
        player.origin_profile_id == profile.pk,
        profile.first_year <= built.season_year <= profile.last_year,
        player.draft_class_id == draft_class.pk,
        draft_class.season_year == built.season_year,
        draft_class.profile_id == profile.pk,
        player.universe_key == built.universe_key,
        player.name == built.name,
        player.birth_year == built.birth_year,
        player.birth_day == built.birth_day,
        player.position == built.position,
        player.handedness == built.handedness,
        player.height_in == built.height_in,
        player.weight_lb == built.weight_lb,
        player.cohort == UniversePlayer.Cohort.DRAFT,
        player.peak_age == built.peak_age,
        player.development_rate == built.development_rate,
        player.decline_rate == built.decline_rate,
        player.origin_franchise_id is None,
        player.origin_draft_round is None,
        player.origin_draft_pick is None,
        player.board_rank is None,
        player.secondary_position_1 is None,
        player.secondary_position_2 is None,
        player.trait_1 is None,
        player.trait_2 is None,
        player.trait_3 is None,
    ))


def _prospect_matches(player, built, draft_class, profile):
    _prospect_years(built)
    if (
        built.draft_round is not None
        or built.draft_pick is not None
        or built.destination_slug is not None
        or built.origin_franchise_slug is not None
        or built.board_rank is not None
        or built.origin_draft_year is not None
    ):
        raise ProspectGenerationError(
            'The approved prospect builder assigned a draft destination.'
        )
    return _generated_prospect_matches(player, built, draft_class, profile)


def _write_prospects(release, seed):
    profiles = [
        EraRuleProfile(release=release, **values)
        for values in later_profile_values()
    ]
    _save_all(profiles)
    stored_profiles = list(release.era_profiles.order_by('first_year'))
    classes = []
    for count in draft_classes():
        profile = _profile_for_year(stored_profiles, count.season_year)
        if profile is None or profile.draft_rounds != count.rounds:
            raise ProspectGenerationError(
                f'No approved profile covers the {count.season_year} draft class.'
            )
        classes.append(DraftClass(
            release=release,
            profile=profile,
            season_year=count.season_year,
            franchise_count=count.franchise_count,
            rounds=count.rounds,
            draftable_count=count.draftable_count,
            undrafted_multiplier=UNDRAFTED_MULTIPLIER,
            undrafted_count=count.undrafted_count,
        ))
    _save_all(classes)
    classes_by_year = {
        row.season_year: row
        for row in release.draft_classes.select_related('profile')
    }
    colleges = {college.slug: college for college in release.colleges.all()}
    college_ids = _require_stored_relation_ids(release, colleges, classes_by_year)
    built_rows = build_prospects(seed, release.slug)
    draftable, undrafted, prospects = prospect_totals()
    if len(built_rows) != prospects:
        raise ProspectGenerationError('The prospect total drifted from 19,777.')
    seen_keys = set()
    players = []
    draftable_seen = 0
    undrafted_seen = 0
    for built in built_rows:
        if built.universe_key in seen_keys:
            raise ProspectGenerationError('Prospect keys must be unique.')
        seen_keys.add(built.universe_key)
        draft_class = classes_by_year[built.season_year]
        profile = draft_class.profile
        if not (profile.first_year <= built.season_year <= profile.last_year):
            raise ProspectGenerationError(
                'A prospect origin profile does not cover its class year.'
            )
        first_pro_year, origin_draft_year = _prospect_years(built)
        if built.draft_pool == DRAFT_POOL_DRAFTABLE:
            draftable_seen += 1
        else:
            undrafted_seen += 1
        college = colleges.get(built.college_slug)
        if college is None or college.name != built.college_name:
            raise ProspectGenerationError(
                'A prospect college is not one of the opening writer\'s 64 colleges.'
            )
        player = UniversePlayer(
            release=release,
            college=college,
            origin_franchise=None,
            draft_class=draft_class,
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
            cohort=UniversePlayer.Cohort.DRAFT,
            draft_pool=built.draft_pool,
            first_pro_year=first_pro_year,
            origin_draft_year=origin_draft_year,
            origin_draft_round=None,
            origin_draft_pick=None,
            peak_age=built.peak_age,
            development_rate=built.development_rate,
            decline_rate=built.decline_rate,
            board_rank=None,
            trait_1=None,
            trait_2=None,
            trait_3=None,
        )
        if not _generated_prospect_matches(player, built, draft_class, profile):
            raise ProspectGenerationError(
                'A generated prospect does not match the approved class, pool, '
                'body, or null-field rules.'
            )
        players.append(player)
    if (
        len(seen_keys) != len(players)
        or draftable_seen != draftable
        or undrafted_seen != undrafted
    ):
        raise ProspectGenerationError(
            'The draftable and undrafted totals drifted from 16,183 and 3,594.'
        )
    _insert_players(players, release, college_ids, classes_by_year)


def _save_all(rows):
    """Full validation for the small profile and draft-class sets."""
    for row in rows:
        try:
            row.full_clean()
        except ValidationError as exc:
            raise ProspectGenerationError(
                f'A generated row failed validation: {exc}'
            ) from exc
    if rows:
        alias = database_alias(rows[0])
        release_id = parent_release_id(rows[0], using=alias)
        if release_id is None:
            raise ProspectGenerationError('A generated row has no parent release.')
        assert_release_lock_held(release_id, using=alias)
        type(rows[0]).objects.bulk_create(rows, batch_size=PLAYER_BATCH_SIZE)


def _cached_related(instance, name):
    """Return a related object already loaded on the instance.

    A missing cache entry is untrusted. This never queries.
    """
    return instance._state.fields_cache.get(name)


def _prospect_relation_exclude():
    actual = frozenset(
        field.name
        for field in UniversePlayer._meta.concrete_fields
        if field.is_relation
    )
    if actual != _PROSPECT_RELATION_FIELDS:
        raise ProspectGenerationError(
            'UniversePlayer foreign keys changed. Prospect validation must name each one.'
        )
    return _PROSPECT_RELATION_FIELDS


def _require_stored_relation_ids(release, colleges, classes_by_year):
    """Require stored ids before any prospect row is constructed."""
    if release.pk is None:
        raise ProspectGenerationError('The target release is not stored.')
    if len(colleges) != len(COLLEGES):
        raise ProspectGenerationError(
            'The prospect writer expected the opening writer\'s 64 colleges.'
        )
    college_ids = set()
    for college in colleges.values():
        if college.pk is None or college.release_id != release.pk:
            raise ProspectGenerationError(
                'A prospect college is missing its stored id.'
            )
        college_ids.add(college.pk)
    if len(college_ids) != len(COLLEGES):
        raise ProspectGenerationError(
            'The prospect writer expected the opening writer\'s 64 colleges.'
        )
    if len(classes_by_year) != SEASON_COUNT:
        raise ProspectGenerationError(
            'The prospect writer expected 60 stored draft classes.'
        )
    for year, draft_class in classes_by_year.items():
        profile = _cached_related(draft_class, 'profile')
        if (
            draft_class.pk is None
            or draft_class.release_id != release.pk
            or draft_class.season_year != year
            or draft_class.profile_id is None
            or profile is None
            or profile.pk is None
            or profile.pk != draft_class.profile_id
            or profile.release_id != release.pk
            or not (profile.first_year <= year <= profile.last_year)
        ):
            raise ProspectGenerationError(
                'A draft class or origin profile is missing its stored id.'
            )
    return college_ids


def _require_trusted_prospect_relations(player, release, college_ids, classes_by_year):
    """Check excluded foreign keys against objects already held in memory."""
    cached_release = _cached_related(player, 'release')
    if (
        release.pk is None
        or player.release_id != release.pk
        or cached_release is None
        or cached_release.pk != release.pk
    ):
        raise ProspectGenerationError(
            'A generated prospect release is not the locked release.'
        )
    if (
        player.origin_franchise_id is not None
        or _cached_related(player, 'origin_franchise') is not None
    ):
        raise ProspectGenerationError(
            'A generated prospect origin franchise must be null.'
        )
    college = _cached_related(player, 'college')
    if (
        college is None
        or college.pk is None
        or player.college_id != college.pk
        or college.pk not in college_ids
        or college.release_id != release.pk
    ):
        raise ProspectGenerationError(
            'A generated prospect college is not one of the stored colleges.'
        )
    draft_class = _cached_related(player, 'draft_class')
    stored_class = (
        classes_by_year.get(draft_class.season_year)
        if draft_class is not None
        else None
    )
    if (
        draft_class is None
        or draft_class.pk is None
        or player.draft_class_id != draft_class.pk
        or stored_class is None
        or stored_class.pk != draft_class.pk
        or stored_class.release_id != release.pk
        or stored_class.season_year != draft_class.season_year
    ):
        raise ProspectGenerationError(
            'A generated prospect draft class is not the stored class for its year.'
        )
    profile = _cached_related(player, 'origin_profile')
    stored_profile = _cached_related(stored_class, 'profile')
    if (
        profile is None
        or profile.pk is None
        or stored_profile is None
        or stored_profile.pk is None
        or player.origin_profile_id != profile.pk
        or profile.pk != stored_profile.pk
        or profile.release_id != release.pk
        or stored_profile.release_id != release.pk
        or stored_class.profile_id != stored_profile.pk
        or not (
            stored_profile.first_year
            <= stored_class.season_year
            <= stored_profile.last_year
        )
    ):
        raise ProspectGenerationError(
            'A generated prospect origin profile does not cover its class year.'
        )


def _validate_generated_players(rows):
    """Scalar clean_fields, clean, and extra_clean, without relation queries.

    ``full_clean`` excludes the trusted foreign keys, and it still runs
    ``clean_fields()`` for every other field, ``clean()``, and
    ``UniversePlayer.extra_clean()``. Unique and check constraints stay off
    here. The insert enforces those, plus the foreign keys.
    """
    exclude = _prospect_relation_exclude()
    for row in rows:
        try:
            row.full_clean(
                exclude=exclude,
                validate_unique=False,
                validate_constraints=False,
            )
        except ValidationError as exc:
            raise ProspectGenerationError(
                f'A generated row failed validation: {exc}'
            ) from exc


def _insert_players(rows, release, college_ids, classes_by_year):
    for row in rows:
        _require_trusted_prospect_relations(
            row,
            release,
            college_ids,
            classes_by_year,
        )
    _validate_generated_players(rows)
    assert_release_lock_held(release.pk, using=database_alias(release))
    try:
        UniversePlayer.objects.bulk_create(rows, batch_size=PLAYER_BATCH_SIZE)
    except IntegrityError as exc:
        raise ProspectGenerationError(
            'A prospect insert violated a database constraint. '
            'The writer rolled back the profiles, classes, and players.'
        ) from exc
