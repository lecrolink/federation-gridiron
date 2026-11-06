"""Read-only publication gate for one draft release.

Cap-on profiles and the two v1 fingerprints are separate checks. The
confirmed fg-founding-2 release also has to match the documented horizon,
membership alignment, expansion rows, quota sums, population totals, and
staff-signature uniqueness. ``publishable`` is true only when every
required gate is implemented and passes.

This module does not save a digest, change status or published_at, create
a timeline, or modify release children. Rules that do not have one
documented expected value are listed on ``ambiguous_requirements`` and are
not treated as passed.
"""

from typing import NamedTuple

from django.db.models import Count, Q

from universe.fingerprints import (
    CATALOG_SCHEME,
    SCHEME_BY_GENERATOR,
    VALIDATION_SCHEME,
    FingerprintSchemeError,
    release_digests,
)
from universe.generation import schedule as confirmed_schedule
from universe.generation.player_counts import (
    FIRST_SEASON_YEAR,
    LAST_SEASON_YEAR,
    POSITIONS,
    SEASON_COUNT,
    UNDRAFTED_MULTIPLIER,
    active_quota,
    draft_classes,
    draft_rounds,
    opening_cohort_count,
    opening_free_agent_count,
    opening_rostered_count,
    prospect_totals,
    quota_total,
    taxi_quota,
)
from universe.models import FranchiseSeasonMembership, UniverseFranchise
from universe.people import POSITIONS as MODEL_POSITIONS
from universe.people import UniversePlayer
from universe.rules import (
    ACTIVE_QUOTA_FIELDS,
    CAP_STATUS,
    TAXI_QUOTA_FIELDS,
    ExpansionClub,
)

CAP_ON = 'on'
if CAP_ON not in CAP_STATUS:
    raise RuntimeError('The stored cap-on status is missing.')
if POSITIONS != MODEL_POSITIONS:
    raise RuntimeError('The documented positions and the model positions differ.')
CONFIRMED_GENERATOR = confirmed_schedule.GENERATOR_VERSION
FOUNDING_SLUGS = tuple(
    f'founding-{number:02d}' for number in range(1, confirmed_schedule.FOUNDING_FRANCHISE_COUNT + 1)
)
ROLE_GM = 'gm'
ROLE_HEAD_COACH = 'head_coach'
EXPANSION_NUMERIC_FIELDS = tuple(
    key for key in confirmed_schedule.EXPANSION_EVENTS[0] if key != 'clubs'
)
DOCUMENTED_ACTIVE_LIMITS = frozenset(
    quota_total(active_quota(year))
    for year in range(FIRST_SEASON_YEAR, LAST_SEASON_YEAR + 1)
)
DOCUMENTED_TAXI_LIMIT = quota_total(taxi_quota(FIRST_SEASON_YEAR))
DRAFTABLE_TOTAL, UNDRAFTED_TOTAL, PROSPECT_TOTAL = prospect_totals()
OPENING_TOTAL = opening_cohort_count()
FREE_AGENT_TOTAL = opening_free_agent_count()
ROSTERED_TOTAL = opening_rostered_count()

if SCHEME_BY_GENERATOR != {
    CONFIRMED_GENERATOR: (CATALOG_SCHEME, VALIDATION_SCHEME),
}:
    raise RuntimeError('The publication gate and the fingerprint map disagree.')
if (DRAFTABLE_TOTAL, UNDRAFTED_TOTAL, PROSPECT_TOTAL, OPENING_TOTAL) != (
    16183, 3594, 19777, 1378,
):
    raise RuntimeError('The documented player totals drifted.')
if confirmed_schedule.MEMBERSHIP_COUNT != 1797:
    raise RuntimeError('The documented membership total drifted.')
if DOCUMENTED_TAXI_LIMIT != 8 or DOCUMENTED_ACTIVE_LIMITS != frozenset((40, 45, 47, 53)):
    raise RuntimeError('The documented roster limits drifted.')

SMOKE_FINGERPRINT_PLACEHOLDERS = frozenset((
    'smoke-catalog-fingerprint',
    'smoke-validation-fingerprint',
))
FINGERPRINT_FIELDS = ('catalog_fingerprint', 'validation_fingerprint')

# These are reported on every evaluation. They do not have one enforceable
# expected value, so they are not required gates and do not flip publishable.
AMBIGUOUS_REQUIREMENTS = (
    'historical_standing_count: the model narrative says 260 rows and the approved fg-founding-2 writer stores none',
    'archive_player_count: 1,118 uses a 5-year mean that the count module calls an assumption',
    'staff_pool_size: the staff proposal says 100 is not a hardcoded publication count',
    'expansion_schedule_version: no documented required integer',
    'opening_contracts: the design counts 1,248 and the approved opening writer does not write them',
    'coordinator_seats: the model sketch counts 104 seats and the approved schedule writer refuses coordinators',
    'college_count: the design says about 48 to 80 schools',
    'minimum_salary and rookie_slot_scale: both documents leave them null until a later number is chosen',
    'staff_career_posts: publication uniqueness uses the generator signature of strengths, weakness, and traits; post rows are not a separate documented key',
    'opening_depth_uniqueness: the design uses one depth index per club and position across starter, backup, and taxi, and the approved opening writer restarts taxi depth at 1',
)


class PublicationEvaluation(NamedTuple):
    """A report. It is not a saved publication.

    ``cap_and_fingerprint_checks_pass`` covers only cap-on profiles and the
    two stored v1 fingerprints. ``publishable`` is true only when that pair
    and every required structural gate pass. An unsupported generator
    version fails closed.
    """

    cap_blockers: tuple
    fingerprint_scheme: tuple
    catalog_digest: object
    validation_digest: object
    catalog_digest_match: object
    validation_digest_match: object
    fingerprint_verification: str
    unverified_placeholder_fields: tuple
    cap_and_fingerprint_checks_pass: bool
    horizon_status: str
    horizon_failures: tuple
    expansion_status: str
    expansion_failures: tuple
    membership_status: str
    membership_failures: tuple
    quota_status: str
    quota_failures: tuple
    population_status: str
    population_failures: tuple
    staff_signature_status: str
    staff_signature_failures: tuple
    ambiguous_requirements: tuple
    publishable: bool
    messages: tuple


def _unsupported(generator_version):
    return (
        'unsupported',
        (
            'The confirmed release gate is not defined for generator version '
            f'{generator_version}.',
        ),
    )


def _quota_map(row, field_names):
    return {
        position: row[field_names[index]]
        for index, position in enumerate(POSITIONS)
    }


def _expansion_arithmetic(row):
    """Shared veteran, rookie, and undrafted arithmetic. Taxi is not a term."""
    failures = []
    if row['veteran_picks_per_new'] * row['new_club_count'] != row['veteran_picks_total']:
        failures.append('veteran pick total')
    if row['clubs_at_max_loss'] + row['clubs_at_min_loss'] != row['existing_club_count']:
        failures.append('loss-club total')
    if (
        row['clubs_at_max_loss'] * row['max_losses_per_existing']
        + row['clubs_at_min_loss'] * row['min_losses_per_existing']
        != row['veteran_picks_total']
    ):
        failures.append('loss distribution')
    if row['min_losses_per_existing'] >= row['max_losses_per_existing']:
        failures.append('loss bounds')
    active_parts = (
        row['veteran_picks_per_new']
        + row['rookie_picks_per_new']
        + row['undrafted_reserved_per_new']
    )
    if active_parts != row['active_target']:
        failures.append('active target')
    if row['undrafted_reserved_per_new'] * row['new_club_count'] > row['undrafted_pool']:
        failures.append('undrafted reserve')
    if row['undrafted_pool'] != row['undrafted_multiplier'] * (
        row['existing_club_count'] + row['new_club_count']
    ):
        failures.append('undrafted pool')
    if row['undrafted_multiplier'] != UNDRAFTED_MULTIPLIER:
        failures.append('undrafted multiplier')
    return failures


for _spec in confirmed_schedule.EXPANSION_EVENTS:
    if _expansion_arithmetic(_spec):
        raise RuntimeError('A documented expansion row breaks its arithmetic.')


def _horizon_gate(release):
    failures = []
    if release.founding_franchise_count != confirmed_schedule.FOUNDING_FRANCHISE_COUNT:
        failures.append(
            f'founding_franchise_count is {release.founding_franchise_count}, not 26.'
        )
    if (
        release.first_season_year != FIRST_SEASON_YEAR
        or release.last_season_year != LAST_SEASON_YEAR
        or release.season_count != SEASON_COUNT
    ):
        failures.append(
            'The playable horizon is '
            f'{release.first_season_year}–{release.last_season_year} '
            f'({release.season_count} seasons), not 1970–2029 (60).'
        )
    franchises = list(release.franchises.values(
        'placeholder_slug',
        'origin',
        'first_active_season',
        'final_active_season',
    ))
    by_slug = {row['placeholder_slug']: row for row in franchises}
    if len(by_slug) != len(franchises) or set(by_slug) != set(confirmed_schedule.DEBUT_YEAR):
        failures.append('The franchise list is not the 32 documented placeholder clubs.')
    else:
        for slug, debut in confirmed_schedule.DEBUT_YEAR.items():
            row = by_slug[slug]
            origin = (
                UniverseFranchise.Origin.FOUNDING
                if debut == FIRST_SEASON_YEAR
                else UniverseFranchise.Origin.EXPANSION
            )
            if (
                row['origin'] != origin
                or row['first_active_season'] != debut
                or row['final_active_season'] is not None
            ):
                failures.append(f'{slug} does not have its documented debut and origin.')
    seats = list(release.opening_staff.values_list(
        'role',
        'franchise__placeholder_slug',
    ))
    for role in (ROLE_GM, ROLE_HEAD_COACH):
        slugs = [slug for seat_role, slug in seats if seat_role == role]
        if sorted(slugs) != sorted(FOUNDING_SLUGS):
            failures.append(
                f'{role} seats are not one per founding franchise.'
            )
    return ('fail' if failures else 'pass', tuple(failures))


def _expansion_gate(release):
    failures = []
    stored = {
        row['season_year']: row
        for row in release.expansion_events.values(
            *EXPANSION_NUMERIC_FIELDS,
            *confirmed_schedule.RULE_FIELDS,
        )
    }
    expected = {
        spec['season_year']: spec
        for spec in confirmed_schedule.EXPANSION_EVENTS
    }
    if set(stored) != set(expected):
        failures.append('Expansion years are not 1976, 1995, 1999, and 2002.')
    for year, spec in expected.items():
        row = stored.get(year)
        if row is None:
            continue
        differing = [
            field for field in EXPANSION_NUMERIC_FIELDS
            if row[field] != spec[field]
        ]
        if differing:
            failures.append(
                f'{year} expansion numbers differ: {", ".join(differing)}.'
            )
        arithmetic = _expansion_arithmetic(row)
        if arithmetic:
            failures.append(
                f'{year} expansion arithmetic failed: {", ".join(arithmetic)}.'
            )
        empty = [
            field for field in confirmed_schedule.RULE_FIELDS
            if not str(row[field] or '').strip()
        ]
        if empty:
            failures.append(f'{year} expansion rule text is empty.')
    clubs = {}
    for row in ExpansionClub.objects.filter(event__release=release).values(
        'event__season_year',
        'selection_order',
        'franchise__placeholder_slug',
    ):
        clubs.setdefault(row['event__season_year'], []).append(
            (row['selection_order'], row['franchise__placeholder_slug'])
        )
    for spec in confirmed_schedule.EXPANSION_EVENTS:
        found = tuple(
            slug for _order, slug in sorted(clubs.get(spec['season_year'], ()))
        )
        if found != spec['clubs']:
            failures.append(
                f'{spec["season_year"]} expansion clubs are not {", ".join(spec["clubs"])}.'
            )
    class_undrafted = {
        row['season_year']: row['undrafted_count']
        for row in release.draft_classes.values('season_year', 'undrafted_count')
    }
    for spec in confirmed_schedule.EXPANSION_EVENTS:
        found = class_undrafted.get(spec['season_year'])
        if found is None or found < spec['undrafted_pool']:
            failures.append(
                f'{spec["season_year"]} undrafted class is smaller than the undrafted pool.'
            )
    return ('fail' if failures else 'pass', tuple(failures))


def _membership_gate(release):
    failures = []
    stored = list(FranchiseSeasonMembership.objects.filter(
        franchise__release=release,
    ).values_list(
        'franchise__placeholder_slug',
        'season_year',
        'conference_slug',
        'division_slug',
        'slot_order',
        'owner_selectable',
    ))
    expected = {
        (
            row['slug'],
            row['season_year'],
            row['conference_slug'],
            row['division_slug'],
            row['slot_order'],
            True,
        )
        for row in confirmed_schedule.membership_rows()
    }
    stored_set = set(stored)
    if len(stored) != len(stored_set):
        failures.append('Franchise-season membership rows are duplicated.')
    missing = len(expected - stored_set)
    extra = len(stored_set - expected)
    if len(stored) != confirmed_schedule.MEMBERSHIP_COUNT or missing or extra:
        failures.append(
            'Memberships are '
            f'{len(stored)} rows, not the documented 1,797 aligned seats '
            f'({missing} missing, {extra} unexpected).'
        )
    if any(not owner_selectable for *_, owner_selectable in stored):
        failures.append('A franchise-season is not owner selectable.')
    return ('fail' if failures else 'pass', tuple(failures))


def _quota_gate(release):
    failures = []
    profiles = list(release.era_profiles.order_by('first_year', 'profile_key').values(
        'profile_key',
        'first_year',
        'last_year',
        'active_limit',
        'taxi_limit',
        'draft_rounds',
        *ACTIVE_QUOTA_FIELDS,
        *TAXI_QUOTA_FIELDS,
    ))
    covered = {}
    for profile in profiles:
        key = profile['profile_key']
        if profile['first_year'] > profile['last_year']:
            failures.append(f'{key} has an empty span.')
            continue
        active = _quota_map(profile, ACTIVE_QUOTA_FIELDS)
        taxi = _quota_map(profile, TAXI_QUOTA_FIELDS)
        if sum(active.values()) != profile['active_limit']:
            failures.append(f'{key} active quotas do not sum to active_limit.')
        if sum(taxi.values()) != profile['taxi_limit']:
            failures.append(f'{key} taxi quotas do not sum to taxi_limit.')
        if (
            profile['active_limit'] not in DOCUMENTED_ACTIVE_LIMITS
            or profile['taxi_limit'] != DOCUMENTED_TAXI_LIMIT
        ):
            failures.append(f'{key} does not use a documented roster limit.')
        in_horizon = FIRST_SEASON_YEAR <= profile['first_year'] <= LAST_SEASON_YEAR
        expected_active = active_quota(profile['first_year']) if in_horizon else None
        expected_taxi = taxi_quota(profile['first_year']) if expected_active else None
        if expected_active is None or active != expected_active or taxi != expected_taxi:
            failures.append(f'{key} quotas are not the documented position fill.')
        if in_horizon and profile['draft_rounds'] != draft_rounds(profile['first_year']):
            failures.append(f'{key} draft length is not the documented round count.')
        reported_overlap = False
        reported_span = False
        for year in range(profile['first_year'], profile['last_year'] + 1):
            if year in covered:
                if not reported_overlap:
                    failures.append(f'{year} is covered by overlapping era profiles.')
                    reported_overlap = True
                continue
            covered[year] = key
            if (
                not reported_span
                and expected_active is not None
                and FIRST_SEASON_YEAR <= year <= LAST_SEASON_YEAR
                and (
                    active_quota(year) != expected_active
                    or taxi_quota(year) != expected_taxi
                    or draft_rounds(year) != profile['draft_rounds']
                )
            ):
                failures.append(f'{key} spans more than one documented quota.')
                reported_span = True
    missing = [
        year for year in range(release.first_season_year, release.last_season_year + 1)
        if year not in covered
    ]
    if missing:
        failures.append('A playable season has no era profile.')
    outside = [
        year for year in covered
        if year < release.first_season_year or year > release.last_season_year
    ]
    if outside:
        failures.append('An era profile extends outside the playable horizon.')
    return ('fail' if failures else 'pass', tuple(failures))


def _population_gate(release):
    failures = []
    expected_classes = {
        row.season_year: row for row in draft_classes()
    }
    stored_classes = {
        row['season_year']: row
        for row in release.draft_classes.values(
            'season_year',
            'franchise_count',
            'rounds',
            'draftable_count',
            'undrafted_multiplier',
            'undrafted_count',
        )
    }
    if set(stored_classes) != set(expected_classes):
        failures.append('Draft classes are not one row for every season from 1970 through 2029.')
    differing_classes = 0
    for year, expected in expected_classes.items():
        found = stored_classes.get(year)
        if found is None:
            continue
        if (
            found['franchise_count'] != expected.franchise_count
            or found['rounds'] != expected.rounds
            or found['draftable_count'] != expected.draftable_count
            or found['undrafted_count'] != expected.undrafted_count
            or found['undrafted_multiplier'] != UNDRAFTED_MULTIPLIER
        ):
            differing_classes += 1
    if differing_classes:
        failures.append(
            f'{differing_classes} draft classes do not match the documented N(year) size.'
        )
    counts = release.players.aggregate(
        opening=Count('id', filter=Q(cohort=UniversePlayer.Cohort.OPENING)),
        draftable=Count(
            'id',
            filter=Q(
                cohort=UniversePlayer.Cohort.DRAFT,
                draft_pool=UniversePlayer.DraftPool.DRAFTABLE,
            ),
        ),
        undrafted=Count(
            'id',
            filter=Q(
                cohort=UniversePlayer.Cohort.DRAFT,
                draft_pool=UniversePlayer.DraftPool.UNDRAFTED,
            ),
        ),
        baked_origin=Count(
            'id',
            filter=Q(cohort=UniversePlayer.Cohort.DRAFT) & (
                Q(origin_draft_round__isnull=False)
                | Q(origin_draft_pick__isnull=False)
                | Q(origin_franchise__isnull=False)
            ),
        ),
    )
    if counts['opening'] != OPENING_TOTAL:
        failures.append(f'Opening cohort is {counts["opening"]}, not 1,378.')
    if counts['draftable'] != DRAFTABLE_TOTAL or counts['undrafted'] != UNDRAFTED_TOTAL:
        failures.append(
            'Prospect totals are '
            f'{counts["draftable"]} draftable and {counts["undrafted"]} undrafted, '
            'not 16,183 and 3,594.'
        )
    if counts['baked_origin']:
        failures.append('A 1970–2029 prospect has an origin draft result.')
    class_counts = {
        (row['draft_class__season_year'], row['draft_pool']): row['n']
        for row in release.players.filter(
            cohort=UniversePlayer.Cohort.DRAFT,
        ).values('draft_class__season_year', 'draft_pool').annotate(n=Count('id'))
    }
    differing_pools = 0
    for year, expected in expected_classes.items():
        draftable = class_counts.get((year, UniversePlayer.DraftPool.DRAFTABLE), 0)
        undrafted = class_counts.get((year, UniversePlayer.DraftPool.UNDRAFTED), 0)
        if draftable != expected.draftable_count or undrafted != expected.undrafted_count:
            differing_pools += 1
    other_pools = [
        key for key in class_counts
        if key[1] not in (
            UniversePlayer.DraftPool.DRAFTABLE,
            UniversePlayer.DraftPool.UNDRAFTED,
        ) or key[0] not in expected_classes
    ]
    if differing_pools or other_pools:
        failures.append('Prospects are not seated in the documented class pools.')
    assignments = list(release.opening_assignments.values(
        'role',
        'depth',
        'franchise_id',
        'player__cohort',
        'franchise__origin',
    ))
    if (
        len(assignments) != OPENING_TOTAL
        or any(row['player__cohort'] != UniversePlayer.Cohort.OPENING for row in assignments)
    ):
        failures.append('Opening assignments are not one per opening player.')
    free_agents = [row for row in assignments if row['role'] == 'free_agent']
    rostered = [row for row in assignments if row['role'] != 'free_agent']
    if len(free_agents) != FREE_AGENT_TOTAL or len(rostered) != ROSTERED_TOTAL:
        failures.append('Opening free-agent and roster counts are not 130 and 1,248.')
    if any(
        row['franchise_id'] is not None or row['depth'] is not None
        for row in free_agents
    ):
        failures.append('A free agent has a franchise or depth.')
    if any(
        row['franchise_id'] is None
        or row['depth'] is None
        or row['franchise__origin'] != UniverseFranchise.Origin.FOUNDING
        for row in rostered
    ):
        failures.append('A rostered opening player is not on a founding franchise.')
    return ('fail' if failures else 'pass', tuple(failures))


def _signature(row):
    return (
        row['strength_1'],
        row['strength_2'],
        row['weakness'],
        row['trait_1'],
        row['trait_2'],
    )


def _duplicate_signatures(rows):
    seen = {}
    duplicates = 0
    for row in rows:
        signature = _signature(row)
        if signature in seen:
            duplicates += 1
        else:
            seen[signature] = row['universe_key']
    return duplicates


def _staff_signature_gate(release):
    fields = (
        'universe_key',
        'strength_1',
        'strength_2',
        'weakness',
        'trait_1',
        'trait_2',
    )
    failures = []
    gm_duplicates = _duplicate_signatures(release.gms.values(*fields))
    coach_duplicates = _duplicate_signatures(release.coaches.values(*fields))
    if gm_duplicates:
        failures.append(
            f'{gm_duplicates} GM profiles repeat a staff signature.'
        )
    if coach_duplicates:
        failures.append(
            f'{coach_duplicates} coach profiles repeat a staff signature.'
        )
    return ('fail' if failures else 'pass', tuple(failures))


def _structural_gates(release):
    if release.generator_version != CONFIRMED_GENERATOR:
        unsupported = _unsupported(release.generator_version)
        return {
            'horizon': unsupported,
            'expansion': unsupported,
            'membership': unsupported,
            'quota': unsupported,
            'population': unsupported,
            'staff_signature': _staff_signature_gate(release),
        }
    return {
        'horizon': _horizon_gate(release),
        'expansion': _expansion_gate(release),
        'membership': _membership_gate(release),
        'quota': _quota_gate(release),
        'population': _population_gate(release),
        'staff_signature': _staff_signature_gate(release),
    }


def _report(
    *,
    cap_blockers,
    fingerprint_scheme,
    catalog_digest,
    validation_digest,
    catalog_digest_match,
    validation_digest_match,
    fingerprint_verification,
    placeholders,
    messages,
    gates,
):
    cap_and_fingerprint_checks_pass = (
        not cap_blockers
        and catalog_digest_match is True
        and validation_digest_match is True
    )
    required = (
        gates['horizon'],
        gates['expansion'],
        gates['membership'],
        gates['quota'],
        gates['population'],
        gates['staff_signature'],
    )
    for name, (status, failures) in gates.items():
        if status == 'pass':
            messages.append(f'{name} passes.')
        else:
            messages.append(f'{name} is {status}.')
            messages.extend(failures)
    publishable = cap_and_fingerprint_checks_pass and all(
        status == 'pass' for status, _failures in required
    )
    messages.append(
        'The release is publishable.'
        if publishable
        else 'The release is not publishable.'
    )
    return PublicationEvaluation(
        cap_blockers=cap_blockers,
        fingerprint_scheme=fingerprint_scheme,
        catalog_digest=catalog_digest,
        validation_digest=validation_digest,
        catalog_digest_match=catalog_digest_match,
        validation_digest_match=validation_digest_match,
        fingerprint_verification=fingerprint_verification,
        unverified_placeholder_fields=placeholders,
        cap_and_fingerprint_checks_pass=cap_and_fingerprint_checks_pass,
        horizon_status=gates['horizon'][0],
        horizon_failures=gates['horizon'][1],
        expansion_status=gates['expansion'][0],
        expansion_failures=gates['expansion'][1],
        membership_status=gates['membership'][0],
        membership_failures=gates['membership'][1],
        quota_status=gates['quota'][0],
        quota_failures=gates['quota'][1],
        population_status=gates['population'][0],
        population_failures=gates['population'][1],
        staff_signature_status=gates['staff_signature'][0],
        staff_signature_failures=gates['staff_signature'][1],
        ambiguous_requirements=AMBIGUOUS_REQUIREMENTS,
        publishable=publishable,
        messages=tuple(messages),
    )


def evaluate_release_publication(release):
    """Report every required publication gate. Do not save the release or its children."""
    cap_blockers = tuple(
        profile.profile_key
        for profile in release.era_profiles.order_by('first_year', 'profile_key')
        if profile.cap_status == CAP_ON and profile.cap_limit is None
    )
    placeholders = tuple(
        field
        for field in FINGERPRINT_FIELDS
        if getattr(release, field) in SMOKE_FINGERPRINT_PLACEHOLDERS
    )
    messages = [
        f'{key} blocks publication because its cap is on and cap_limit is null.'
        for key in cap_blockers
    ]
    gates = _structural_gates(release)
    scheme = SCHEME_BY_GENERATOR.get(release.generator_version)
    if scheme is None:
        messages.append(
            'Fingerprint scheme is unavailable for generator version '
            f'{release.generator_version}.'
        )
        for field in placeholders:
            messages.append(
                f'{field} is an unverified placeholder. The evaluator did not replace it.'
            )
        return _report(
            cap_blockers=cap_blockers,
            fingerprint_scheme=(),
            catalog_digest=None,
            validation_digest=None,
            catalog_digest_match=None,
            validation_digest_match=None,
            fingerprint_verification='unsupported',
            placeholders=placeholders,
            messages=messages,
            gates=gates,
        )
    try:
        catalog_digest, validation_digest = release_digests(release)
    except FingerprintSchemeError as exc:
        messages.append(str(exc))
        return _report(
            cap_blockers=cap_blockers,
            fingerprint_scheme=(),
            catalog_digest=None,
            validation_digest=None,
            catalog_digest_match=None,
            validation_digest_match=None,
            fingerprint_verification='unsupported',
            placeholders=placeholders,
            messages=messages,
            gates=gates,
        )
    catalog_match = release.catalog_fingerprint == catalog_digest
    validation_match = release.validation_fingerprint == validation_digest
    messages.append(
        f'catalog-sha256-v1 {catalog_digest} '
        f'{"matches" if catalog_match else "does not match"} the stored catalog fingerprint.'
    )
    messages.append(
        f'validation-sha256-v1 {validation_digest} '
        f'{"matches" if validation_match else "does not match"} the stored validation fingerprint.'
    )
    for field in placeholders:
        messages.append(
            f'{field} is an unverified placeholder. The evaluator did not replace it.'
        )
    return _report(
        cap_blockers=cap_blockers,
        fingerprint_scheme=scheme,
        catalog_digest=catalog_digest,
        validation_digest=validation_digest,
        catalog_digest_match=catalog_match,
        validation_digest_match=validation_match,
        fingerprint_verification=(
            'match' if catalog_match and validation_match else 'mismatch'
        ),
        placeholders=placeholders,
        messages=messages,
        gates=gates,
    )
