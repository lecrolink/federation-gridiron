"""Write one rating profile for each playable player on an fg-founding-2 draft.

The release must already have its schedule, 64 colleges, 12 era profiles,
60 draft classes, 1,378 opening players, and 19,777 prospects. This module
adds exactly 21,155 PlayerRatingProfile rows. An identical rerun changes
nothing. A different seed, a partial profile set, an extra row, or any
mismatched owned row is refused, and nothing is deleted.

Timelines, current ratings, scouting, development events, overall ratings,
contracts, and Joe LeCrone are not written. Models and the pure rating
builder are not modified.
"""

from django.core.exceptions import ValidationError
from django.db import DEFAULT_DB_ALIAS, IntegrityError

# universe.models must load before timelines. Importing Timeline first enters
# universe.people while universe.models is unfinished, and models then imports
# universe.rules, which needs names people has not defined yet.
from universe.models import UniverseRelease
from timelines.models import Timeline
from universe.release_guard import (
    assert_release_lock_held,
    database_alias,
    holding_release_lock,
)
from universe.generation.founding import (
    FoundingGenerationError,
    _resolve_release_id,
    _validate_seed,
)
from universe.generation.player_identities import (
    DRAFT_POOL_DRAFTABLE,
    DRAFT_POOL_UNDRAFTED,
    SLOT_BACKUP,
    SLOT_FREE_AGENT,
    SLOT_STARTER,
    SLOT_TAXI,
)
from universe.generation.player_ratings import (
    MAXIMUM_POTENTIAL,
    MINIMUM_RATING,
    PLAYABLE_RATING_COUNT,
    RATING_COMPONENTS,
    RatingBuildError,
    build_playable_ratings,
)
from universe.generation.prospects import (
    ProspectGenerationError,
    _opening_matches,
    _prospect_state,
    _require_schedule,
)
from universe.people import PLAYER_COMPONENTS, PlayerRatingProfile, UniversePlayer


# Same batch size as the opening and prospect writers. Do not lower it:
# a batch of one would repeat an insert once per playable player.
PLAYER_BATCH_SIZE = 500
_PROFILE_RELATION_FIELDS = frozenset(('player',))
_OPENING_ROLES = frozenset((
    SLOT_STARTER,
    SLOT_BACKUP,
    SLOT_TAXI,
    SLOT_FREE_AGENT,
))
_PROSPECT_POOLS = frozenset((
    DRAFT_POOL_DRAFTABLE,
    DRAFT_POOL_UNDRAFTED,
))
_PREREQUISITE = (
    'The draft release must already have the fg-founding-2 schedule, '
    'colleges, 12 era profiles, 60 draft classes, 1,378 opening players, '
    'and 19,777 prospects. The rating writer will not delete or replace them.'
)
_PROFILE_DIFFER = (
    'Existing rating profiles differ from this release. '
    'The writer will not delete or replace them.'
)


class PlayerRatingGenerationError(Exception):
    """The rating profiles cannot be written."""


def generate_player_ratings(league_id, seed):
    """Write the rating profiles. Return ``created`` or ``unchanged``."""
    try:
        _validate_seed(seed)
        release_id = _resolve_release_id(league_id)
    except FoundingGenerationError as exc:
        raise PlayerRatingGenerationError(str(exc)) from exc
    try:
        with holding_release_lock(release_id, using=DEFAULT_DB_ALIAS) as release:
            metadata = _release_metadata(release)
            _refuse_closed(release)
            _translate_schedule(release)
            if release.seed != seed:
                raise PlayerRatingGenerationError(
                    'The rating writer will not change the release seed.'
                )
            _require_known_fields()
            _require_playable_release(release, seed)
            ratings = _playable_ratings(seed, release.slug)
            players = _stored_players(release)
            _require_player_keys(players, ratings)
            state = _rating_state(release, ratings)
            if state == 'match':
                return 'unchanged'
            if state == 'differ':
                raise PlayerRatingGenerationError(_PROFILE_DIFFER)
            _write_profiles(release, ratings, players)
            if _rating_state(release, ratings) != 'match':
                raise PlayerRatingGenerationError(
                    'The rating profiles did not match the approved builder.'
                )
            release.refresh_from_db()
            if _release_metadata(release) != metadata:
                raise PlayerRatingGenerationError(
                    'The rating writer changed the release metadata.'
                )
            return 'created'
    except IntegrityError as exc:
        raise PlayerRatingGenerationError(
            'A rating profile insert violated a database constraint. '
            'The writer rolled back the rating profiles.'
        ) from exc
    except (RuntimeError, ValueError) as exc:
        raise PlayerRatingGenerationError(str(exc)) from exc


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
    if release.status != release.Status.DRAFT or release.published_at is not None:
        raise PlayerRatingGenerationError('A published release cannot be changed.')
    if Timeline.objects.filter(release_id=release.pk).exists():
        raise PlayerRatingGenerationError('A timeline points at this release.')


def _translate_schedule(release):
    try:
        _require_schedule(release)
    except ProspectGenerationError as exc:
        raise PlayerRatingGenerationError(str(exc)) from exc


def _require_known_fields():
    if RATING_COMPONENTS != PLAYER_COMPONENTS:
        raise PlayerRatingGenerationError(
            'Rating components drifted from the stored profile fields.'
        )
    covered = {'player'}
    for component in PLAYER_COMPONENTS:
        covered.add(f'initial_{component}')
        covered.add(f'potential_{component}')
    missing = []
    for field in PlayerRatingProfile._meta.concrete_fields:
        if field.name == 'id' or field.name in covered:
            continue
        missing.append(f'PlayerRatingProfile.{field.name}')
    if missing:
        raise PlayerRatingGenerationError(
            'A required model field has no approved value: ' + ', '.join(missing)
        )


def _require_playable_release(release, seed):
    try:
        opening_matches = _opening_matches(release, seed)
        prospect_state = _prospect_state(release, seed)
    except ProspectGenerationError as exc:
        raise PlayerRatingGenerationError(str(exc)) from exc
    if not opening_matches or prospect_state != 'match':
        raise PlayerRatingGenerationError(_PREREQUISITE)


def _playable_ratings(seed, release_slug):
    try:
        ratings = build_playable_ratings(seed, release_slug)
    except RatingBuildError as exc:
        raise PlayerRatingGenerationError(str(exc)) from exc
    if len(ratings) != PLAYABLE_RATING_COUNT:
        raise PlayerRatingGenerationError(
            'The rating builder did not return the 21,155 playable identities.'
        )
    return ratings


def _stored_players(release):
    return list(
        UniversePlayer.objects.filter(release_id=release.pk).select_related('release')
    )


def _require_player_keys(players, ratings):
    stored = [player.universe_key for player in players]
    expected = [rating.universe_key for rating in ratings]
    if len(stored) != len(expected) or set(stored) != set(expected):
        raise PlayerRatingGenerationError(_PROFILE_DIFFER)


def _rating_state(release, ratings):
    stored = list(
        PlayerRatingProfile.objects.filter(player__release_id=release.pk)
        .select_related('player')
    )
    if not stored:
        return 'absent'
    expected = {rating.universe_key: rating for rating in ratings}
    if len(stored) != len(expected) or len(expected) != PLAYABLE_RATING_COUNT:
        return 'differ'
    seen = set()
    for profile in stored:
        key = profile.player.universe_key
        rating = expected.get(key)
        if (
            rating is None
            or profile.player.release_id != release.pk
            or key in seen
            or not _same_rating(profile, rating)
        ):
            return 'differ'
        seen.add(key)
    if seen != set(expected):
        return 'differ'
    return 'match'


def _same_rating(profile, rating):
    if len(rating.components) != len(RATING_COMPONENTS):
        return False
    for component in rating.components:
        if (
            getattr(profile, f'initial_{component.component}') != component.initial
            or getattr(profile, f'potential_{component.component}') != component.potential
        ):
            return False
    return True


def _write_profiles(release, ratings, players):
    by_key = {player.universe_key: player for player in players}
    if len(by_key) != len(players):
        raise PlayerRatingGenerationError(_PROFILE_DIFFER)
    rows = []
    for rating in ratings:
        player = by_key.get(rating.universe_key)
        if player is None:
            raise PlayerRatingGenerationError(_PROFILE_DIFFER)
        _require_player_cohort(player, rating)
        profile = PlayerRatingProfile(player=player, **_rating_values(rating))
        rows.append(profile)
    if len(rows) != PLAYABLE_RATING_COUNT:
        raise PlayerRatingGenerationError(
            'The rating writer did not build 21,155 profiles.'
        )
    _insert_profiles(rows, release)


def _require_player_cohort(player, rating):
    if rating.cohort in _OPENING_ROLES:
        matches = (
            player.cohort == UniversePlayer.Cohort.OPENING
            and player.draft_pool is None
        )
    elif rating.cohort in _PROSPECT_POOLS:
        matches = (
            player.cohort == UniversePlayer.Cohort.DRAFT
            and player.draft_pool == rating.cohort
        )
    else:
        matches = False
    if not matches:
        raise PlayerRatingGenerationError(
            'A stored player cohort does not match its rating identity.'
        )


def _rating_values(rating):
    values = {}
    seen = set()
    for component in rating.components:
        name = component.component
        if name in seen or name not in RATING_COMPONENTS:
            raise PlayerRatingGenerationError(
                'A generated rating repeats or omits a component.'
            )
        seen.add(name)
        _require_scalar(name, component.initial, component.potential)
        values[f'initial_{name}'] = component.initial
        values[f'potential_{name}'] = component.potential
    if seen != set(RATING_COMPONENTS):
        raise PlayerRatingGenerationError(
            'A generated rating repeats or omits a component.'
        )
    return values


def _require_scalar(component, initial, potential):
    if (
        isinstance(initial, bool)
        or isinstance(potential, bool)
        or not isinstance(initial, int)
        or not isinstance(potential, int)
        or initial < MINIMUM_RATING
        or potential > MAXIMUM_POTENTIAL
        or initial > potential
    ):
        raise PlayerRatingGenerationError(
            f'A generated {component} rating is outside 1–96.'
        )


def _cached_related(instance, name):
    """Return a related object already loaded on the instance. Never fetches."""
    return instance._state.fields_cache.get(name)


def _relation_exclude():
    actual = frozenset(
        field.name
        for field in PlayerRatingProfile._meta.concrete_fields
        if field.is_relation
    )
    if actual != _PROFILE_RELATION_FIELDS:
        raise PlayerRatingGenerationError(
            'PlayerRatingProfile foreign keys changed. '
            'Rating validation must name each one.'
        )
    return _PROFILE_RELATION_FIELDS


def _require_trusted_profile(profile, release):
    """Check the player and its release from objects already in memory."""
    player = _cached_related(profile, 'player')
    if (
        player is None
        or player.pk is None
        or profile.player_id != player.pk
        or player.release_id != release.pk
    ):
        raise PlayerRatingGenerationError(
            'A generated rating profile player is not a stored playable player.'
        )
    cached_release = _cached_related(player, 'release')
    if (
        release.pk is None
        or cached_release is None
        or cached_release.pk != release.pk
        or cached_release.status != release.Status.DRAFT
    ):
        raise PlayerRatingGenerationError(
            'A generated rating profile release is not the locked draft release.'
        )


def _validate_profiles(rows):
    """Scalar clean_fields, clean, and extra_clean, without relation queries."""
    exclude = _relation_exclude()
    for row in rows:
        try:
            row.full_clean(
                exclude=exclude,
                validate_unique=False,
                validate_constraints=False,
            )
        except ValidationError as exc:
            raise PlayerRatingGenerationError(
                f'A generated row failed validation: {exc}'
            ) from exc


def _insert_profiles(rows, release):
    for row in rows:
        _require_trusted_profile(row, release)
        for component in RATING_COMPONENTS:
            _require_scalar(
                component,
                getattr(row, f'initial_{component}'),
                getattr(row, f'potential_{component}'),
            )
    _validate_profiles(rows)
    assert_release_lock_held(release.pk, using=database_alias(release))
    try:
        PlayerRatingProfile.objects.bulk_create(rows, batch_size=PLAYER_BATCH_SIZE)
    except IntegrityError as exc:
        raise PlayerRatingGenerationError(
            'A rating profile insert violated a database constraint. '
            'The writer rolled back the rating profiles.'
        ) from exc
