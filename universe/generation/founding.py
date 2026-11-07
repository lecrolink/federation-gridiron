"""Draft-only founding catalog for generator version fg-founding-1.

The command fills an existing draft ``UniverseRelease``. It does not create
the league, the release, conferences, divisions, or teams, and it does not
publish. Catalog and validation fingerprints are left as they already are:
the approved plan describes a catalog hash but does not lock the byte layout
or a test vector.

Keys and attributes are HMAC-SHA256 of the decimal seed. The seed's UTF-8
bytes are the key and the coordinate's UTF-8 bytes are the message. Names
take the first eight digest bytes as a big-endian integer, modulo the frozen
list length. A key coordinate names the role and the pool index. It includes
the release slug so the globally unique ``universe_key`` does not collide
across releases, and it does not include the release primary key or a
timeline id.

Age in the first season is 32 through 65. The 24 employed head coaches, pool
indexes 0 through 23, use the older half of that band, 49 through 65. Every
other person in this pool uses the full band. Head-coach specialty for this
pool is ``both``.
"""

import hashlib
import hmac

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import DEFAULT_DB_ALIAS, IntegrityError
from django.db.models import Q
from django.db.models.deletion import ProtectedError

from leagues.models import League
from timelines.models import Timeline
from universe.generation.names import GIVEN_NAMES, SURNAMES
from universe.models import (
    FranchiseHistoryEvent,
    FranchiseSeasonMembership,
    HistoricalStanding,
    UniverseFranchise,
    UniverseRelease,
)
from universe.release_guard import (
    assert_release_lock_held,
    database_alias,
    holding_release_lock,
    parent_release_id,
)
from universe.people import (
    COACH_TAGS,
    COACH_TRAITS,
    DEFENSE_SCHEMES,
    GM_PRIORITIES,
    GM_TAGS,
    GM_TRAITS,
    CoachRatingProfile,
    GMRatingProfile,
    UniverseAward,
    UniverseCoach,
    UniverseGM,
    UniversePlayer,
    UniverseProSeason,
    UniverseProStint,
    UniverseStaffIdentity,
    UniverseStaffPost,
)
from universe.rules import ExpansionClub, OpeningAssignment, OpeningStaffAssignment

GENERATOR_VERSION = 'fg-founding-1'
POOL_SIZE = 100
OPENING_SEATS = 24
FOUNDING_FRANCHISE_COUNT = 24
FIRST_SEASON_YEAR = 1970
SEASON_COUNT = 60
LAST_SEASON_YEAR = 2029

AGE_MIN = 32
AGE_MAX = 65
EMPLOYED_HEAD_COACH_AGE_MIN = 49

ROLE_GM = 'gm'
ROLE_HEAD_COACH = 'head_coach'
OPENING_ROLES = (ROLE_GM, ROLE_HEAD_COACH)
ROLE_PREFIX = {
    ROLE_GM: 'g-',
    ROLE_HEAD_COACH: 'c-',
}

OFFENSE_AT_FOUNDING = ('power_run', 'option', 'pro_style')
HEAD_COACH_SPECIALTY = UniverseCoach.Specialty.BOTH

GM_FORMER_ROLES = tuple(UniverseGM.FormerRole.values)
COACH_FORMER_ROLES = tuple(UniverseCoach.FormerRole.values)

FOUNDING_DIVISIONS = (
    ('conference-1', 'c1-d1'),
    ('conference-1', 'c1-d2'),
    ('conference-1', 'c1-d3'),
    ('conference-2', 'c2-d1'),
    ('conference-2', 'c2-d2'),
    ('conference-2', 'c2-d3'),
)

MAX_SIGNATURE_ATTEMPTS = 64
_SEED_MIN = -2 ** 63
_SEED_MAX = 2 ** 63 - 1


class FoundingGenerationError(Exception):
    """The founding slice cannot be written."""


def hmac_int(seed, coordinate):
    """First eight HMAC-SHA256 bytes, as a big-endian integer."""
    digest = hmac.new(
        str(seed).encode('utf-8'),
        str(coordinate).encode('utf-8'),
        hashlib.sha256,
    ).digest()
    return int.from_bytes(digest[:8], 'big')


def key_coordinate(release_slug, role, index):
    return f'staff:{role}:{release_slug}:{index:03d}'


def universe_key(seed, release_slug, role, index):
    prefix = ROLE_PREFIX[role]
    digest = hmac.new(
        str(seed).encode('utf-8'),
        key_coordinate(release_slug, role, index).encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()
    return prefix + digest[:62]


def choose_name(seed, staff_key):
    given = GIVEN_NAMES[hmac_int(seed, f'{staff_key}:given_name') % len(GIVEN_NAMES)]
    surname = SURNAMES[hmac_int(seed, f'{staff_key}:surname') % len(SURNAMES)]
    return f'{given} {surname}'


def _age_bounds(role, index):
    if role == ROLE_HEAD_COACH and index < OPENING_SEATS:
        return EMPLOYED_HEAD_COACH_AGE_MIN, AGE_MAX
    return AGE_MIN, AGE_MAX


def birth_year_for(seed, staff_key, role, index, season_year):
    low, high = _age_bounds(role, index)
    age = low + (hmac_int(seed, f'{staff_key}:birth_year') % (high - low + 1))
    return season_year - age


def _coordinate(staff_key, field, attempt):
    if attempt:
        return f'{staff_key}:{field}:{attempt}'
    return f'{staff_key}:{field}'


def _pick(seed, staff_key, field, options, attempt):
    return options[hmac_int(seed, _coordinate(staff_key, field, attempt)) % len(options)]


def _pick_pair(seed, staff_key, field_a, field_b, options, attempt):
    first = _pick(seed, staff_key, field_a, options, attempt)
    rest = tuple(option for option in options if option != first)
    second = rest[
        hmac_int(seed, _coordinate(staff_key, field_b, attempt)) % len(rest)
    ]
    return first, second


def _pick_excluding(seed, staff_key, field, options, banned, attempt):
    rest = tuple(option for option in options if option not in banned)
    return rest[hmac_int(seed, _coordinate(staff_key, field, attempt)) % len(rest)]


def founding_franchises(season_year):
    """The 24 documented 1970 slots, in slot-table order."""
    rows = []
    for division_index, (conference_slug, division_slug) in enumerate(FOUNDING_DIVISIONS):
        for slot_order in range(1, 5):
            number = division_index * 4 + slot_order
            rows.append({
                'slug': f'founding-{number:02d}',
                'display_name': f'Founding franchise {number:02d}',
                'conference_slug': conference_slug,
                'division_slug': division_slug,
                'slot_order': slot_order,
                'season_year': season_year,
                'first_active_season': season_year,
            })
    return rows


def _tag_draw(seed, staff_key, role, attempt):
    if role == ROLE_GM:
        tag_options = GM_TAGS
        trait_options = GM_TRAITS
    else:
        tag_options = COACH_TAGS
        trait_options = COACH_TRAITS
    strength_1, strength_2 = _pick_pair(
        seed, staff_key, 'strength_1', 'strength_2', tag_options, attempt,
    )
    weakness = _pick_excluding(
        seed, staff_key, 'weakness', tag_options, (strength_1, strength_2), attempt,
    )
    trait_1, trait_2 = _pick_pair(
        seed, staff_key, 'trait_1', 'trait_2', trait_options, attempt,
    )
    return {
        'strength_1': strength_1,
        'strength_2': strength_2,
        'weakness': weakness,
        'trait_1': trait_1,
        'trait_2': trait_2,
    }


def _signature(tags):
    return (
        tags['strength_1'],
        tags['strength_2'],
        tags['weakness'],
        tags['trait_1'],
        tags['trait_2'],
    )


def build_staff(seed, release_slug, season_year):
    """One hundred GM records, then one hundred head-coach records."""
    rows = []
    for role, former_roles in (
        (ROLE_GM, GM_FORMER_ROLES),
        (ROLE_HEAD_COACH, COACH_FORMER_ROLES),
    ):
        seen = set()
        for index in range(POOL_SIZE):
            staff_key = universe_key(seed, release_slug, role, index)
            record = {
                'role': role,
                'index': index,
                'universe_key': staff_key,
                'name': choose_name(seed, staff_key),
                'birth_year': birth_year_for(
                    seed, staff_key, role, index, season_year,
                ),
                'debut_year': season_year,
                'former_role': _pick(
                    seed, staff_key, 'former_role', former_roles, 0,
                ),
            }
            if role == ROLE_GM:
                priority_1, priority_2 = _pick_pair(
                    seed,
                    staff_key,
                    'positional_priority_1',
                    'positional_priority_2',
                    GM_PRIORITIES,
                    0,
                )
                record['positional_priority_1'] = priority_1
                record['positional_priority_2'] = priority_2
            else:
                record['specialty'] = HEAD_COACH_SPECIALTY
                record['preferred_offense'] = _pick(
                    seed, staff_key, 'preferred_offense', OFFENSE_AT_FOUNDING, 0,
                )
                record['preferred_defense'] = _pick(
                    seed, staff_key, 'preferred_defense', DEFENSE_SCHEMES, 0,
                )
            placed = False
            for attempt in range(MAX_SIGNATURE_ATTEMPTS):
                tags = _tag_draw(seed, staff_key, role, attempt)
                signature = _signature(tags)
                if signature in seen:
                    continue
                seen.add(signature)
                record.update(tags)
                record['signature_attempt'] = attempt
                placed = True
                break
            if not placed:
                raise FoundingGenerationError(
                    f'No unique {role} signature for index {index}.'
                )
            rows.append(record)
    return rows


def build_release_slice(seed, release_slug, season_year):
    staff = build_staff(seed, release_slug, season_year)
    return {
        'franchises': founding_franchises(season_year),
        'staff': staff,
        'keys': {row['universe_key'] for row in staff},
    }


def _validate_seed(seed):
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise FoundingGenerationError('The seed must be an integer.')
    if seed < _SEED_MIN or seed > _SEED_MAX:
        raise FoundingGenerationError('The seed does not fit the release seed field.')


def _resolve_release_id(league_id):
    if not League.objects.filter(pk=league_id).exists():
        raise FoundingGenerationError('No league has that id.')
    drafts = UniverseRelease.objects.filter(
        root_league_id=league_id,
        status=UniverseRelease.Status.DRAFT,
    )
    count = drafts.count()
    if count == 1:
        return drafts.get().pk
    if count > 1:
        raise FoundingGenerationError('This league has more than one draft release.')
    if UniverseRelease.objects.filter(
        root_league_id=league_id,
        status=UniverseRelease.Status.PUBLISHED,
    ).exists():
        raise FoundingGenerationError('This league\'s release is published.')
    raise FoundingGenerationError('This league has no draft release.')


def _require_horizon(release):
    if (
        release.founding_franchise_count != FOUNDING_FRANCHISE_COUNT
        or release.first_season_year != FIRST_SEASON_YEAR
        or release.season_count != SEASON_COUNT
        or release.last_season_year != LAST_SEASON_YEAR
    ):
        raise FoundingGenerationError(
            'The draft release must use 24 founding franchises and the '
            '1970–2029 horizon.'
        )


def _owned_keys(seed, release_slug):
    return {
        universe_key(seed, release_slug, role, index)
        for role in OPENING_ROLES
        for index in range(POOL_SIZE)
    }


def _replacement_keys(release):
    if release.generator_version != GENERATOR_VERSION:
        if (
            release.staff_identities.exists()
            or release.gms.exists()
            or release.coaches.exists()
            or release.opening_staff.exists()
        ):
            raise FoundingGenerationError(
                'This draft uses another generator version and already has '
                'staff. This generator will not guess which rows belong to it.'
            )
        return set()
    return _owned_keys(release.seed, release.slug)


def _slice_matches(release, expected):
    franchises = {
        franchise.placeholder_slug: franchise
        for franchise in release.franchises.filter(
            placeholder_slug__in=[row['slug'] for row in expected['franchises']],
        )
    }
    if set(franchises) != {row['slug'] for row in expected['franchises']}:
        return False
    for row in expected['franchises']:
        franchise = franchises[row['slug']]
        if any((
            franchise.display_name != row['display_name'],
            franchise.origin != UniverseFranchise.Origin.FOUNDING,
            franchise.first_active_season != row['first_active_season'],
            franchise.final_active_season is not None,
            franchise.catalog_team_id is not None,
            franchise.history_predecessor_id is not None,
        )):
            return False
        memberships = list(franchise.memberships.all())
        if len(memberships) != 1:
            return False
        membership = memberships[0]
        if any((
            membership.season_year != row['season_year'],
            membership.conference_slug != row['conference_slug'],
            membership.division_slug != row['division_slug'],
            membership.slot_order != row['slot_order'],
            membership.owner_selectable is not True,
        )):
            return False

    identities = {
        identity.universe_key: identity
        for identity in UniverseStaffIdentity.objects.filter(
            release=release,
            universe_key__in=expected['keys'],
        ).select_related('gm_profile', 'coach_profile')
    }
    if set(identities) != expected['keys']:
        return False

    gm_rows = [row for row in expected['staff'] if row['role'] == ROLE_GM]
    coach_rows = [row for row in expected['staff'] if row['role'] == ROLE_HEAD_COACH]
    if not _profiles_match(identities, gm_rows, 'gm_profile', _gm_values):
        return False
    if not _profiles_match(identities, coach_rows, 'coach_profile', _coach_values):
        return False
    return _assignments_match(release, expected['franchises'], gm_rows, coach_rows)


def _has_profile(identity, accessor):
    try:
        getattr(identity, accessor)
    except ObjectDoesNotExist:
        return False
    return True


def _profiles_match(identities, rows, accessor, values):
    for row in rows:
        identity = identities[row['universe_key']]
        if identity.name != row['name'] or identity.birth_year != row['birth_year']:
            return False
        if accessor == 'gm_profile':
            present = _has_profile(identity, 'gm_profile')
            other = _has_profile(identity, 'coach_profile')
        else:
            present = _has_profile(identity, 'coach_profile')
            other = _has_profile(identity, 'gm_profile')
        if not present or other:
            return False
        profile = getattr(identity, accessor)
        if values(profile) != values_from_row(row, accessor):
            return False
    return True


def _gm_values(profile):
    return (
        profile.universe_key,
        profile.name,
        profile.birth_year,
        profile.debut_year,
        profile.former_role,
        profile.positional_priority_1,
        profile.positional_priority_2,
        profile.strength_1,
        profile.strength_2,
        profile.weakness,
        profile.trait_1,
        profile.trait_2,
    )


def _coach_values(profile):
    return (
        profile.universe_key,
        profile.name,
        profile.birth_year,
        profile.debut_year,
        profile.specialty,
        profile.former_role,
        profile.preferred_offense,
        profile.preferred_defense,
        profile.strength_1,
        profile.strength_2,
        profile.weakness,
        profile.trait_1,
        profile.trait_2,
    )


def values_from_row(row, accessor):
    shared = (
        row['universe_key'],
        row['name'],
        row['birth_year'],
        row['debut_year'],
    )
    tags = (
        row['strength_1'],
        row['strength_2'],
        row['weakness'],
        row['trait_1'],
        row['trait_2'],
    )
    if accessor == 'gm_profile':
        return shared + (
            row['former_role'],
            row['positional_priority_1'],
            row['positional_priority_2'],
        ) + tags
    return shared + (
        row['specialty'],
        row['former_role'],
        row['preferred_offense'],
        row['preferred_defense'],
    ) + tags


def _assignments_match(release, franchises, gm_rows, coach_rows):
    expected = {}
    for row, staff in (
        *(((franchise['slug'], ROLE_GM), gm_rows[index]) for index, franchise in enumerate(franchises)),
        *(((franchise['slug'], ROLE_HEAD_COACH), coach_rows[index]) for index, franchise in enumerate(franchises)),
    ):
        expected[row] = staff['universe_key']
    found = {}
    assignments = OpeningStaffAssignment.objects.filter(
        release=release,
        franchise__placeholder_slug__in=[franchise['slug'] for franchise in franchises],
    ).select_related('franchise', 'gm', 'coach')
    for assignment in assignments:
        slug = assignment.franchise.placeholder_slug
        if assignment.role == ROLE_GM:
            if assignment.coach_id is not None or assignment.gm_id is None:
                return False
            person_key = assignment.gm.universe_key
        elif assignment.role == ROLE_HEAD_COACH:
            if assignment.gm_id is not None or assignment.coach_id is None:
                return False
            person_key = assignment.coach.universe_key
        else:
            return False
        found[(slug, assignment.role)] = person_key
    return found == expected


def _refuse_blockers(release, franchises, old_keys):
    franchise_ids = [franchise.pk for franchise in franchises]
    if any(franchise.catalog_team_id or franchise.history_predecessor_id for franchise in franchises):
        raise FoundingGenerationError(
            'A founding franchise is linked to a catalog team or a predecessor.'
        )
    if any(franchise.origin != UniverseFranchise.Origin.FOUNDING for franchise in franchises):
        raise FoundingGenerationError('A founding slug is not marked as founding.')
    if franchise_ids and FranchiseSeasonMembership.objects.filter(
        franchise_id__in=franchise_ids,
    ).exclude(season_year=FIRST_SEASON_YEAR).exists():
        raise FoundingGenerationError(
            'A founding franchise has a membership outside 1970.'
        )
    blockers = (
        ('history event', FranchiseHistoryEvent.objects.filter(franchise_id__in=franchise_ids)),
        ('historical standing', HistoricalStanding.objects.filter(franchise_id__in=franchise_ids)),
        ('player stint', UniverseProStint.objects.filter(franchise_id__in=franchise_ids)),
        ('player season', UniverseProSeason.objects.filter(franchise_id__in=franchise_ids)),
        ('staff post', UniverseStaffPost.objects.filter(franchise_id__in=franchise_ids)),
        ('expansion club', ExpansionClub.objects.filter(franchise_id__in=franchise_ids)),
        ('opening player assignment', OpeningAssignment.objects.filter(franchise_id__in=franchise_ids)),
    )
    if franchise_ids:
        for label, queryset in blockers:
            if queryset.exists():
                raise FoundingGenerationError(
                    f'A founding franchise has a {label} this generator does not own.'
                )
        if UniversePlayer.objects.filter(origin_franchise_id__in=franchise_ids).exists():
            raise FoundingGenerationError(
                'A founding franchise is a player origin this generator does not own.'
            )
        if UniverseAward.objects.filter(franchise_id__in=franchise_ids).exists():
            raise FoundingGenerationError(
                'A founding franchise has an award this generator does not own.'
            )

    coordinator = OpeningStaffAssignment.objects.filter(release=release).exclude(
        role__in=OPENING_ROLES,
    ).filter(
        Q(franchise_id__in=franchise_ids)
        | Q(gm__universe_key__in=old_keys)
        | Q(coach__universe_key__in=old_keys)
    )
    if coordinator.exists():
        raise FoundingGenerationError(
            'A coordinator seat is attached to this generator\'s slice.'
        )
    if franchise_ids and not old_keys:
        if OpeningStaffAssignment.objects.filter(franchise_id__in=franchise_ids).exists():
            raise FoundingGenerationError(
                'Opening staff are attached to founding franchises this version cannot identify.'
            )
    if franchise_ids and old_keys:
        foreign_seat = OpeningStaffAssignment.objects.filter(
            franchise_id__in=franchise_ids,
        ).exclude(
            Q(gm__universe_key__in=old_keys) | Q(coach__universe_key__in=old_keys),
        )
        if foreign_seat.exists():
            raise FoundingGenerationError(
                'An opening seat on a founding franchise points outside this generator.'
            )
    if not old_keys:
        return

    both_profiles = UniverseStaffIdentity.objects.filter(
        release=release,
        universe_key__in=old_keys,
        gm_profile__isnull=False,
        coach_profile__isnull=False,
    )
    if both_profiles.exists():
        raise FoundingGenerationError(
            'A generated identity has both a GM profile and a coach profile.'
        )
    if GMRatingProfile.objects.filter(gm__universe_key__in=old_keys).exists():
        raise FoundingGenerationError('A generated GM has a rating profile.')
    if CoachRatingProfile.objects.filter(coach__universe_key__in=old_keys).exists():
        raise FoundingGenerationError('A generated coach has a rating profile.')
    if UniverseStaffPost.objects.filter(
        Q(gm__universe_key__in=old_keys) | Q(coach__universe_key__in=old_keys),
    ).exists():
        raise FoundingGenerationError('A generated person has a staff post.')
    if UniverseAward.objects.filter(
        Q(gm__universe_key__in=old_keys) | Q(coach__universe_key__in=old_keys),
    ).exists():
        raise FoundingGenerationError('A generated person has an award.')


def _delete_slice(release, franchises, old_keys):
    assert_release_lock_held(release.pk, using=database_alias(release))
    franchise_ids = [franchise.pk for franchise in franchises]
    if old_keys:
        OpeningStaffAssignment.objects.filter(
            Q(gm__universe_key__in=old_keys) | Q(coach__universe_key__in=old_keys),
        ).delete()
        UniverseStaffIdentity.objects.filter(
            release=release,
            universe_key__in=old_keys,
        ).delete()
    if franchise_ids:
        FranchiseSeasonMembership.objects.filter(franchise_id__in=franchise_ids).delete()
        UniverseFranchise.objects.filter(pk__in=franchise_ids).delete()


def _save_all(rows):
    for row in rows:
        try:
            row.full_clean()
        except ValidationError as exc:
            raise FoundingGenerationError(
                f'A generated row failed validation: {exc}'
            ) from exc
    if rows:
        alias = database_alias(rows[0])
        release_id = parent_release_id(rows[0], using=alias)
        if release_id is None:
            raise FoundingGenerationError('A generated row has no parent release.')
        assert_release_lock_held(release_id, using=alias)
        type(rows[0]).objects.bulk_create(rows)


def _write_slice(release, expected):
    franchises = [
        UniverseFranchise(
            release=release,
            placeholder_slug=row['slug'],
            display_name=row['display_name'],
            origin=UniverseFranchise.Origin.FOUNDING,
            first_active_season=row['first_active_season'],
            final_active_season=None,
            catalog_team=None,
            history_predecessor=None,
        )
        for row in expected['franchises']
    ]
    _save_all(franchises)
    stored_franchises = {
        franchise.placeholder_slug: franchise
        for franchise in release.franchises.filter(
            placeholder_slug__in=[row['slug'] for row in expected['franchises']],
        )
    }
    memberships = [
        FranchiseSeasonMembership(
            franchise=stored_franchises[row['slug']],
            season_year=row['season_year'],
            conference_slug=row['conference_slug'],
            division_slug=row['division_slug'],
            slot_order=row['slot_order'],
            owner_selectable=True,
        )
        for row in expected['franchises']
    ]
    _save_all(memberships)

    identities = [
        UniverseStaffIdentity(
            release=release,
            universe_key=row['universe_key'],
            name=row['name'],
            birth_year=row['birth_year'],
        )
        for row in expected['staff']
    ]
    _save_all(identities)
    stored_identities = {
        identity.universe_key: identity
        for identity in UniverseStaffIdentity.objects.filter(
            release=release,
            universe_key__in=expected['keys'],
        )
    }
    profiles = []
    for row in expected['staff']:
        identity = stored_identities[row['universe_key']]
        if row['role'] == ROLE_GM:
            profiles.append(UniverseGM(
                release=release,
                identity=identity,
                universe_key=row['universe_key'],
                name=row['name'],
                birth_year=row['birth_year'],
                debut_year=row['debut_year'],
                former_role=row['former_role'],
                positional_priority_1=row['positional_priority_1'],
                positional_priority_2=row['positional_priority_2'],
                strength_1=row['strength_1'],
                strength_2=row['strength_2'],
                weakness=row['weakness'],
                trait_1=row['trait_1'],
                trait_2=row['trait_2'],
            ))
        else:
            profiles.append(UniverseCoach(
                release=release,
                identity=identity,
                universe_key=row['universe_key'],
                name=row['name'],
                birth_year=row['birth_year'],
                debut_year=row['debut_year'],
                specialty=row['specialty'],
                former_role=row['former_role'],
                preferred_offense=row['preferred_offense'],
                preferred_defense=row['preferred_defense'],
                strength_1=row['strength_1'],
                strength_2=row['strength_2'],
                weakness=row['weakness'],
                trait_1=row['trait_1'],
                trait_2=row['trait_2'],
            ))
    gm_profiles = [profile for profile in profiles if isinstance(profile, UniverseGM)]
    coach_profiles = [profile for profile in profiles if isinstance(profile, UniverseCoach)]
    _save_all(gm_profiles)
    _save_all(coach_profiles)
    stored_gms = {
        gm.universe_key: gm
        for gm in UniverseGM.objects.filter(release=release, universe_key__in=expected['keys'])
    }
    stored_coaches = {
        coach.universe_key: coach
        for coach in UniverseCoach.objects.filter(
            release=release,
            universe_key__in=expected['keys'],
        )
    }
    assignments = []
    for index, franchise_row in enumerate(expected['franchises']):
        franchise = stored_franchises[franchise_row['slug']]
        gm_key = next(
            row['universe_key']
            for row in expected['staff']
            if row['role'] == ROLE_GM and row['index'] == index
        )
        coach_key = next(
            row['universe_key']
            for row in expected['staff']
            if row['role'] == ROLE_HEAD_COACH and row['index'] == index
        )
        assignments.append(OpeningStaffAssignment(
            release=release,
            franchise=franchise,
            role=ROLE_GM,
            gm=stored_gms[gm_key],
            coach=None,
        ))
        assignments.append(OpeningStaffAssignment(
            release=release,
            franchise=franchise,
            role=ROLE_HEAD_COACH,
            gm=None,
            coach=stored_coaches[coach_key],
        ))
    _save_all(assignments)


def _slice_existed(release, franchises, old_keys):
    if franchises:
        return True
    if not old_keys:
        return False
    return UniverseStaffIdentity.objects.filter(
        release=release,
        universe_key__in=old_keys,
    ).exists()


def generate_founding_release(league_id, seed):
    """Write this generator's slice. Return created, rebuilt, or unchanged."""
    _validate_seed(seed)
    release_id = _resolve_release_id(league_id)
    try:
        with holding_release_lock(release_id, using=DEFAULT_DB_ALIAS) as release:
            if release.status != UniverseRelease.Status.DRAFT:
                raise FoundingGenerationError('A published release cannot be changed.')
            _require_horizon(release)
            if Timeline.objects.filter(release=release).exists():
                raise FoundingGenerationError('A timeline points at this release.')
            expected = build_release_slice(seed, release.slug, release.first_season_year)
            rows_match = _slice_matches(release, expected)
            metadata_match = (
                release.generator_version == GENERATOR_VERSION
                and release.seed == seed
            )
            if rows_match and metadata_match:
                return 'unchanged'
            if rows_match:
                release.generator_version = GENERATOR_VERSION
                release.seed = seed
                release.save(update_fields=['generator_version', 'seed'])
                return 'rebuilt'
            franchises = list(release.franchises.filter(
                placeholder_slug__in=[row['slug'] for row in expected['franchises']],
            ))
            old_keys = _replacement_keys(release)
            _refuse_blockers(release, franchises, old_keys)
            existed = _slice_existed(release, franchises, old_keys)
            _delete_slice(release, franchises, old_keys)
            _write_slice(release, expected)
            release.generator_version = GENERATOR_VERSION
            release.seed = seed
            release.save(update_fields=['generator_version', 'seed'])
            return 'rebuilt' if existed else 'created'
    except ProtectedError as exc:
        raise FoundingGenerationError(
            'Another row still references this generator\'s slice.'
        ) from exc
    except IntegrityError as exc:
        raise FoundingGenerationError(
            'A generated key already exists outside this release.'
        ) from exc
