"""Record division places from one timeline's closed regular-season records.

The caller supplies wins, losses, and ties for every franchise that has a
membership in the season just played. Clubs are grouped by that season's
stored conference and division. Rank is winning percentage only. A tie counts
as half a win and half a loss. A club whose percentage is unique receives
place ``1 +`` the number of clubs in that division with a strictly higher
percentage. Every club that shares a percentage stores a null place. A record
with no games has no percentage and stores a null place.

Slug, ``slot_order``, draft order, HMAC, and NFL tie-break steps are not
used. The service does not read the next season's membership. It does not
read or write phase flags: there is no season-close flag separate from the
phase chain, and those flags are left as they are. The precondition is an
active timeline, a ``TimelineSeason`` for the supplied year, and exactly one
nonnegative record for every membership in that year.

An exact repeat writes nothing. A partial stored set, or a repeat that would
change a stored record or place, is refused and leaves the stored rows as
they are. Shared franchise identity, membership, opponent pairings, week
assignments, historical standings, and draft order are not written.
"""

from django.core.exceptions import ValidationError
from django.db import DEFAULT_DB_ALIAS, IntegrityError

from timelines.models import Timeline, TimelineDivisionPlace, TimelineSeason
from universe.models import FranchiseSeasonMembership, UniverseFranchise, UniverseRelease
from universe.release_guard import holding_release_lock


_RECORD_FIELDS = ('franchise_id', 'wins', 'losses', 'ties')
_COUNT_MAX = 32767


class DivisionPlaceRefused(Exception):
    """The places were not recorded and this call kept every stored row."""


class DivisionPlaceResult:
    """``recorded`` inserted the season. ``unchanged`` wrote nothing."""

    def __init__(self, outcome, timeline_id, season_id, row_count, placed_count):
        self.outcome = outcome
        self.timeline_id = timeline_id
        self.season_id = season_id
        self.row_count = row_count
        self.placed_count = placed_count

    @property
    def unresolved_count(self):
        return self.row_count - self.placed_count


def record_division_places(*, timeline_id, season_year, records):
    """Record places for one timeline season, or return unchanged."""
    parsed = _parse_records(records)
    try:
        release_id = Timeline.objects.values_list('release_id', flat=True).get(
            pk=timeline_id,
        )
    except Timeline.DoesNotExist:
        raise DivisionPlaceRefused('The timeline is missing.') from None
    try:
        with holding_release_lock(release_id=release_id, using=DEFAULT_DB_ALIAS) as release:
            try:
                return _record_locked(release, timeline_id, season_year, parsed)
            except ValidationError as exc:
                raise DivisionPlaceRefused(
                    'Division places refused: %s' % exc
                ) from exc
            except IntegrityError as exc:
                raise DivisionPlaceRefused(
                    'Division places refused: the rows conflict with a stored record.'
                ) from exc
    except UniverseRelease.DoesNotExist:
        raise DivisionPlaceRefused('The timeline is missing.') from None


def _record_locked(release, timeline_id, season_year, parsed):
    timeline = _lock_timeline(timeline_id)
    if timeline.release_id != release.pk:
        _refuse('The timeline is not on this release.')
    if timeline.status != Timeline.Status.ACTIVE:
        _refuse('An active timeline can record division places.')
    season = _lock_season(timeline, season_year)
    memberships = _memberships(release, season_year)
    _require_same_franchises(release, parsed, memberships)
    places = _places(memberships, parsed)
    stored = _lock_stored(timeline, season)
    if stored:
        _require_closed_match(stored, parsed, places)
        return _result('unchanged', timeline, season, places)
    rows = []
    for franchise_id, (wins, losses, ties) in parsed.items():
        row = TimelineDivisionPlace(
            timeline=timeline,
            season=season,
            franchise_id=franchise_id,
            wins=wins,
            losses=losses,
            ties=ties,
            division_place=places[franchise_id],
        )
        row.full_clean()
        rows.append(row)
    if rows:
        TimelineDivisionPlace.objects.bulk_create(rows)
        return _result('recorded', timeline, season, places)
    return _result('unchanged', timeline, season, places)


def _parse_records(records):
    if not isinstance(records, (list, tuple)):
        _refuse('Division places need one record per membership.')
    parsed = {}
    for record in records:
        franchise_id, counts = _parse_record(record)
        if franchise_id in parsed:
            _refuse('A franchise is repeated.')
        parsed[franchise_id] = counts
    return parsed


def _parse_record(record):
    if not isinstance(record, dict) or set(record) != set(_RECORD_FIELDS):
        _refuse('Wins, losses, and ties are nonnegative whole numbers.')
    franchise_id = _whole(record['franchise_id'])
    if franchise_id < 1:
        _refuse('Wins, losses, and ties are nonnegative whole numbers.')
    counts = tuple(_whole(record[name]) for name in ('wins', 'losses', 'ties'))
    return franchise_id, counts


def _whole(value):
    if isinstance(value, bool) or not isinstance(value, int):
        _refuse('Wins, losses, and ties are nonnegative whole numbers.')
    if value < 0 or value > _COUNT_MAX:
        _refuse('Wins, losses, and ties are nonnegative whole numbers.')
    return value


def _lock_timeline(timeline_id):
    try:
        return Timeline.objects.select_for_update().get(pk=timeline_id)
    except Timeline.DoesNotExist:
        _refuse('The timeline is missing.')


def _lock_season(timeline, season_year):
    if isinstance(season_year, bool) or not isinstance(season_year, int):
        _refuse('The season is not on this timeline.')
    try:
        return TimelineSeason.objects.select_for_update().get(
            timeline=timeline,
            season_year=season_year,
        )
    except TimelineSeason.DoesNotExist:
        _refuse('The season is not on this timeline.')


def _memberships(release, season_year):
    return tuple(
        FranchiseSeasonMembership.objects.filter(
            franchise__release_id=release.pk,
            season_year=season_year,
        ).values_list('franchise_id', 'conference_slug', 'division_slug')
    )


def _require_same_franchises(release, parsed, memberships):
    expected = {franchise_id for franchise_id, _conference, _division in memberships}
    supplied = set(parsed)
    if supplied == expected:
        return
    unknown = supplied - expected
    if unknown:
        owners = dict(
            UniverseFranchise.objects.filter(pk__in=unknown).values_list(
                'pk',
                'release_id',
            )
        )
        for franchise_id in unknown:
            owner = owners.get(franchise_id)
            if owner is not None and owner != release.pk:
                _refuse('A record names a franchise from another release.')
        _refuse('A record names a franchise that has no membership this season.')
    _refuse('A membership this season has no record.')


def _places(memberships, parsed):
    groups = {}
    for franchise_id, conference, division in memberships:
        groups.setdefault((conference, division), []).append(franchise_id)
    places = {}
    for franchise_ids in groups.values():
        keys = {
            franchise_id: _percentage_key(parsed[franchise_id])
            for franchise_id in franchise_ids
        }
        for franchise_id, key in keys.items():
            if key is None or _share_count(keys, key) != 1:
                places[franchise_id] = None
                continue
            ahead = sum(
                1
                for other in keys.values()
                if other is not None and _higher(other, key)
            )
            places[franchise_id] = 1 + ahead
    return places


def _percentage_key(counts):
    wins, losses, ties = counts
    games = wins + losses + ties
    if games == 0:
        return None
    return (2 * wins + ties, games)


def _share_count(keys, key):
    return sum(1 for other in keys.values() if other is not None and _same(other, key))


def _same(left, right):
    return left[0] * right[1] == right[0] * left[1]


def _higher(left, right):
    return left[0] * right[1] > right[0] * left[1]


def _lock_stored(timeline, season):
    return tuple(
        TimelineDivisionPlace.objects.select_for_update().filter(
            timeline=timeline,
            season=season,
        )
    )


def _require_closed_match(stored, parsed, places):
    if {row.franchise_id for row in stored} != set(parsed) or len(stored) != len(parsed):
        _refuse('The stored division places are a partial set.')
    for row in stored:
        wins, losses, ties = parsed[row.franchise_id]
        if (row.wins, row.losses, row.ties, row.division_place) != (
            wins,
            losses,
            ties,
            places[row.franchise_id],
        ):
            _refuse('The stored division places do not match this record.')


def _result(outcome, timeline, season, places):
    return DivisionPlaceResult(
        outcome,
        timeline.pk,
        season.pk,
        len(places),
        sum(1 for place in places.values() if place is not None),
    )


def _refuse(message):
    raise DivisionPlaceRefused(message)
