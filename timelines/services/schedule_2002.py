"""Timeline fixtures for the 2002 regular season.

Each club plays 16 games: six division games, home and away; four games
against one rotating same-conference division; four games against one
rotating other-conference division; and two same-place games against the
other two same-conference divisions.

Stored seats ``d1``, ``d2``, ``d3``, and ``d4`` are the rotation positions
East, North, South, and West, in that order, in each conference. Those
words are rotation labels. The slugs are not geographic divisions.
``conference-1`` takes the row the fact books list as AFC.
``conference-2`` takes the row they list as NFC. Those are rotation labels
too. Swapping the conferences would change the interconference pairs.

``k3 = (year - 2002) mod 3`` picks the intraconference pairs. Both
conferences use the same pairs.

- 0, years 2002, 2005, 2008, 2011, 2014, 2017, 2020: East–West and North–South
- 1, years 2003, 2006, 2009, 2012, 2015, 2018: East–South and North–West
- 2, years 2004, 2007, 2010, 2013, 2016, 2019: East–North and South–West

``k4 = (year - 2002) mod 4`` picks the interconference pairs. The AFC-side
positions East, North, South, and West play the NFC-side positions:

- 0, years 2002, 2006, 2010, 2014, 2018: North, South, East, West
- 1, years 2003, 2007, 2011, 2015, 2019: East, West, South, North
- 2, years 2004, 2008, 2012, 2016, 2020: West, East, North, South
- 3, years 2005, 2009, 2013, 2017: South, North, West, East

The other conference is that matching read in reverse. The two same-place
games are against the two same-conference divisions that are not the
intraconference partner. First plays first, and so on.

Home sites for a four-game set use the stored ``slot_order``. Slots 1–2
are one pair and slots 3–4 are the other. On the first meeting of two
divisions, pair 1–2 hosts both clubs in the other division's pair 1–2 and
visits both clubs in pair 3–4. Pair 3–4 does the reverse. Each later
meeting of that same division pairing flips every site. The intraconference
repeat is 3 years and the interconference repeat is 4. The meeting index
is ``(year - first_year) // period``. An odd index is the flipped
orientation. The 2002 start is that first-meeting orientation.

Same-place games give every club in a division the same home division and
the same away division, one home and one away. On the first meeting, ``d1``
hosts the lower-numbered of its two remaining opponents. The other three
sites are then the only orientation that still gives every division one
home and one away. The next repeat of that intraconference pairing reverses
all four sites. Position order is the seat order ``d1``, ``d2``, ``d3``,
``d4``.

This service writes the 2002 fixtures on their own path. Seasons 2003
through 2020 use one later path. Its prior year is ``season_year - 1``,
and its fixtures come from ``planned_games``. That path stores a season
only when the prior year already has a read-only division-place check.
Those checks are the supported close, 2002 through 2004, so the service
stores 2003, 2004, and 2005. A later year whose prior season has no check
is refused
before any insert. ``planned_games`` can still project 2006–2020. Same-place
games read ``TimelineDivisionPlace`` from that prior played season. They
do not read ``TimelineSchedulingRank``,
``TimelinePriorFinish``, ``slot_order`` as a place, slug order, draft
order, or a new random seed. A timeline that starts in 2003 or later has
no approved opening 2002 scheduling places, so this service does not
invent one and does not schedule 2003 for that start.

A timeline that starts in 2001 or earlier must already have a completed
2001 season, the complete prior-finish set, and the complete scheduling-rank
set. Same-place games read ``TimelineSchedulingRank``. They do not read a
2001 division place, and this service does not create a schedule seed for
that timeline. A timeline that starts in 2002 or later did not play 2001.
Its same-place games use a stored opening permutation of each 2002
division. The permutation is HMAC-SHA256 of the release seed and
``opening-2001-place:{conference}:{division}:{franchise slug}:{index}``.
Place 1 is the lowest digest. The timeline id is not an input. The row is
a 2002 schedule seed, not a played standing and not a 2001 finish.

An empty finish table does not mean the season was unplayed. The timeline's
start season decides which source is required.
"""

from collections import Counter, namedtuple

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from timelines.models import (
    ScheduleGame,
    Timeline,
    TimelinePriorFinish,
    TimelineScheduleSeed,
    TimelineSchedulingDraw,
    TimelineSchedulingRank,
    TimelineSeason,
)
from timelines.services.prior_finish import (
    PriorFinishRefused,
    derive_scheduling_ranks,
)
from timelines.services.division_place_2002 import (
    SUPPORTED_SEASONS,
    DivisionPlaceRefused,
    verify_played_places,
)
from universe.generation.founding import hmac_int
from universe.models import FranchiseSeasonMembership
from universe.release_guard import assert_release_lock_held, holding_release_lock


FIRST_SEASON = 2002
LAST_SEASON = 2020
DRAW_LIMIT = 8

POSITIONS = ('d1', 'd2', 'd3', 'd4')
POSITION_INDEX = {slug: index for index, slug in enumerate(POSITIONS)}
AFC_SIDE = 'conference-1'
NFC_SIDE = 'conference-2'

INTRA_PAIRS = (
    ((0, 3), (1, 2)),
    ((0, 2), (1, 3)),
    ((0, 1), (2, 3)),
)
INTER_ROW = (
    (1, 2, 0, 3),
    (0, 3, 2, 1),
    (3, 0, 1, 2),
    (2, 1, 3, 0),
)

LATER_SEASON = (
    'The prior season has no verified division-place check.'
)
NO_OPENING_2002_PLACES = (
    'A timeline that starts in 2003 or later has no approved 2002 '
    'scheduling places.'
)
INCOMPLETE_2001 = 'The 2001 regular season is not complete.'
INCOMPLETE_FINISHES = 'The 2001 prior-finish set is incomplete.'
INCOMPLETE_RANKS = 'The 2002 scheduling ranks are incomplete.'
MISMATCHED_TRANSITION = (
    'Stored 2001 finishes, draws, or scheduling ranks do not match.'
)
PLAYED_SEED = 'A timeline that includes 2001 does not use a schedule seed.'
SHAPE = 'The season does not have eight divisions of four.'

Seat = namedtuple('Seat', ('franchise_id', 'slug', 'conference', 'division', 'slot'))
Fixture = namedtuple('Fixture', ('home_id', 'away_id', 'category'))


class TimelineScheduleRefused(Exception):
    """The timeline schedule cannot be written."""


def generate_timeline_schedule(*, timeline_id, season_year):
    """Write one 2002–2020 season. Return ``created`` or ``unchanged``.

    2002 keeps its opening path. 2003–2020 use the prior season's verified
    places when that check exists. A later year without one is refused
    before the release lock.
    """
    _require_id(timeline_id)
    if isinstance(season_year, bool) or not isinstance(season_year, int):
        raise TimelineScheduleRefused('The season is outside 2002–2020.')
    if season_year == FIRST_SEASON:
        return _generate_for(timeline_id, _generate_locked)
    if FIRST_SEASON < season_year <= LAST_SEASON:
        if (season_year - 1) not in _PRIOR_PLACE_VERIFIERS:
            raise TimelineScheduleRefused(LATER_SEASON)
        return _generate_for(
            timeline_id,
            lambda release, locked_id, year=season_year: (
                _generate_verified_prior(release, locked_id, year)
            ),
        )
    raise TimelineScheduleRefused('The season is outside 2002–2020.')


def _generate_for(timeline_id, writer):
    probe = Timeline.objects.filter(pk=timeline_id).only('pk', 'release_id').first()
    if probe is None:
        raise TimelineScheduleRefused('The timeline is missing.')
    with transaction.atomic():
        with holding_release_lock(probe.release_id) as release:
            return writer(release, timeline_id)


def opening_places(seed, seats):
    """Permute each 2002 division into places 1–4. Place 1 is the lowest digest."""
    groups = _groups(seats)
    places = {}
    for (conference, division), group in sorted(groups.items()):
        ranked = _rank_slugs(
            seed,
            conference,
            division,
            [seat.slug for seat in group],
        )
        by_slug = {seat.slug: seat for seat in group}
        for place, slug in enumerate(ranked, start=1):
            places[by_slug[slug].franchise_id] = place
    return places


def planned_games(season_year, seats, places):
    """Return the 256 fixtures for one documented season.

    ``generate_timeline_schedule`` stores 2002, and stores 2003–2020 when
    the prior year has a read-only division-place check. This function
    still projects every year in that span.
    """
    _require_formula_year(season_year)
    groups = _groups(seats)
    _require_unique_places(seats, places, 'Unresolved prior-season places block this season.')
    games = set()
    _add_division_games(games, groups)
    _add_rotating_games(games, groups, season_year)
    _add_place_games(games, groups, places, season_year)
    if len(games) != 256:
        raise TimelineScheduleRefused('The season did not produce 256 fixtures.')
    return tuple(sorted(games, key=lambda game: (game.category, game.home_id, game.away_id)))


def _generate_locked(release, timeline_id):
    if release.status != release.Status.PUBLISHED:
        raise TimelineScheduleRefused('The release is not published.')
    try:
        timeline = Timeline.objects.select_for_update().get(pk=timeline_id)
    except Timeline.DoesNotExist as exc:
        raise TimelineScheduleRefused('The timeline is missing.') from exc
    if timeline.release_id != release.pk:
        raise TimelineScheduleRefused('The timeline is missing.')
    if timeline.status != Timeline.Status.ACTIVE:
        raise TimelineScheduleRefused('The timeline is not active.')

    current = _load_seats(release.pk, FIRST_SEASON)
    _groups(current)
    if timeline.start_season_year <= 2001:
        places = _places_from_played_2001(release, timeline, current)
        seed_plan = ()
    else:
        places = opening_places(release.seed, current)
        seed_plan = tuple(
            _seed_row(seat, places[seat.franchise_id])
            for seat in sorted(current, key=lambda seat: seat.franchise_id)
        )

    try:
        season = TimelineSeason.objects.select_for_update().get(
            timeline_id=timeline.pk,
            season_year=FIRST_SEASON,
        )
    except TimelineSeason.DoesNotExist as exc:
        raise TimelineScheduleRefused(
            'The timeline has no season for this year.'
        ) from exc
    if season.regular_season_simulated:
        raise TimelineScheduleRefused('The season is already marked simulated.')

    expected = planned_games(FIRST_SEASON, current, places)
    expected_games = {
        (game.home_id, game.away_id, game.category, ScheduleGame.Kind.REGULAR)
        for game in expected
    }
    expected_seeds = _seed_keys(seed_plan)
    stored_games = list(
        ScheduleGame.objects.select_for_update().filter(
            timeline_id=timeline.pk,
            season_year=FIRST_SEASON,
        ).order_by('pk')
    )
    stored_seeds = list(
        TimelineScheduleSeed.objects.select_for_update().filter(
            timeline_id=timeline.pk,
        ).order_by('pk')
    )
    found_games = {
        (row.home_id, row.away_id, row.category, row.kind)
        for row in stored_games
    }
    found_seeds = {_stored_seed_key(row) for row in stored_seeds}
    if found_games == expected_games and found_seeds == expected_seeds:
        return 'unchanged'
    if found_games and found_games != expected_games:
        raise TimelineScheduleRefused('Stored fixtures do not match this season.')
    if found_seeds and found_seeds != expected_seeds:
        if not seed_plan:
            raise TimelineScheduleRefused(PLAYED_SEED)
        raise TimelineScheduleRefused('Stored opening seed does not match this season.')
    if found_games != expected_games and (found_games or found_seeds):
        raise TimelineScheduleRefused('Stored fixtures do not match this season.')

    assert_release_lock_held(release.pk)
    _insert_season(timeline, seed_plan, expected)
    return 'created'


def _prior_place_verifier(season_year):
    def verify(timeline, year=season_year):
        return verify_played_places(timeline, year)
    return verify


_PRIOR_PLACE_VERIFIERS = {
    year: _prior_place_verifier(year) for year in SUPPORTED_SEASONS
}


def _generate_verified_prior(release, timeline_id, season_year):
    """Write one later season from the prior year's verified places."""
    prior_year = season_year - 1
    verify_places = _PRIOR_PLACE_VERIFIERS.get(prior_year)
    if verify_places is None:
        raise TimelineScheduleRefused(LATER_SEASON)
    return _generate_from_prior_places(
        release,
        timeline_id,
        season_year=season_year,
        prior_year=prior_year,
        verify_places=verify_places,
        missing_prior=f'The timeline has no {prior_year} season.',
        require_start_before=2003 if season_year == 2003 else None,
    )


def _generate_from_prior_places(
    release,
    timeline_id,
    *,
    season_year,
    prior_year,
    verify_places,
    missing_prior,
    require_start_before,
):
    if release.status != release.Status.PUBLISHED:
        raise TimelineScheduleRefused('The release is not published.')
    try:
        timeline = Timeline.objects.select_for_update().get(pk=timeline_id)
    except Timeline.DoesNotExist as exc:
        raise TimelineScheduleRefused('The timeline is missing.') from exc
    if timeline.release_id != release.pk:
        raise TimelineScheduleRefused('The timeline is missing.')
    if timeline.status != Timeline.Status.ACTIVE:
        raise TimelineScheduleRefused('The timeline is not active.')
    if (
        require_start_before is not None
        and timeline.start_season_year >= require_start_before
    ):
        raise TimelineScheduleRefused(NO_OPENING_2002_PLACES)
    _lock_named_season(timeline, prior_year, missing_prior)
    _lock_named_season(timeline, season_year, 'The timeline has no season for this year.')
    try:
        places = verify_places(timeline)
    except DivisionPlaceRefused as exc:
        raise TimelineScheduleRefused(str(exc)) from exc
    current = _load_seats(release.pk, season_year)
    expected = planned_games(season_year, current, places)
    expected_games = {
        (game.home_id, game.away_id, game.category, ScheduleGame.Kind.REGULAR)
        for game in expected
    }
    stored_games = list(
        ScheduleGame.objects.select_for_update().filter(
            timeline_id=timeline.pk,
            season_year=season_year,
        ).order_by('pk')
    )
    found_games = {
        (row.home_id, row.away_id, row.category, row.kind)
        for row in stored_games
    }
    if found_games == expected_games:
        return 'unchanged'
    if found_games:
        raise TimelineScheduleRefused('Stored fixtures do not match this season.')
    assert_release_lock_held(release.pk)
    _insert_games(timeline, season_year, expected)
    return 'created'


def _lock_named_season(timeline, season_year, message):
    try:
        return TimelineSeason.objects.select_for_update().get(
            timeline_id=timeline.pk,
            season_year=season_year,
        )
    except TimelineSeason.DoesNotExist as exc:
        raise TimelineScheduleRefused(message) from exc


def _places_from_played_2001(release, timeline, current):
    season = (
        TimelineSeason.objects.select_for_update()
        .filter(timeline_id=timeline.pk, season_year=2001)
        .first()
    )
    if season is None or not season.regular_season_simulated:
        raise TimelineScheduleRefused(INCOMPLETE_2001)
    membership_ids = set(
        FranchiseSeasonMembership.objects.filter(
            franchise__release_id=release.pk,
            season_year=2001,
        ).values_list('franchise_id', flat=True)
    )
    finishes = list(
        TimelinePriorFinish.objects.select_for_update().filter(
            timeline_id=timeline.pk,
            season_year=2001,
        )
    )
    if {row.franchise_id for row in finishes} != membership_ids:
        raise TimelineScheduleRefused(INCOMPLETE_FINISHES)
    ranks = list(
        TimelineSchedulingRank.objects.select_for_update().filter(
            timeline_id=timeline.pk,
            season_year=2002,
        )
    )
    current_ids = {seat.franchise_id for seat in current}
    if {row.franchise_id for row in ranks} != current_ids:
        raise TimelineScheduleRefused(INCOMPLETE_RANKS)
    list(
        TimelineSchedulingDraw.objects.select_for_update().filter(
            timeline_id=timeline.pk,
            season_year=2002,
        )
    )
    try:
        outcome = derive_scheduling_ranks(timeline_id=timeline.pk)
    except PriorFinishRefused as exc:
        raise TimelineScheduleRefused(MISMATCHED_TRANSITION) from exc
    if outcome != 'unchanged':
        raise TimelineScheduleRefused(MISMATCHED_TRANSITION)
    by_seat = {seat.franchise_id: seat for seat in current}
    places = {}
    for row in ranks:
        seat = by_seat[row.franchise_id]
        if (
            row.conference_slug != seat.conference
            or row.division_slug != seat.division
            or row.scheduling_rank not in (1, 2, 3, 4)
        ):
            raise TimelineScheduleRefused(MISMATCHED_TRANSITION)
        places[row.franchise_id] = row.scheduling_rank
    _require_unique_places(current, places, MISMATCHED_TRANSITION)
    return places


def _require_unique_places(seats, places, message):
    grouped = {}
    for seat in seats:
        place = places.get(seat.franchise_id)
        grouped.setdefault((seat.conference, seat.division), []).append(place)
    for found in grouped.values():
        if sorted(place for place in found if place is not None) != [1, 2, 3, 4]:
            raise TimelineScheduleRefused(message)
        if any(place is None for place in found):
            raise TimelineScheduleRefused(message)


def _seed_row(seat, place):
    return {
        'franchise_id': seat.franchise_id,
        'division_place': place,
        'conference_slug': seat.conference,
        'division_slug': seat.division,
    }


def _seed_keys(plan):
    return frozenset(
        (
            row['franchise_id'],
            row['division_place'],
            row['conference_slug'],
            row['division_slug'],
            FIRST_SEASON,
        )
        for row in plan
    )


def _stored_seed_key(row):
    return (
        row.franchise_id,
        row.division_place,
        row.conference_slug,
        row.division_slug,
        row.season_year,
    )


def _insert_season(timeline, seed_plan, expected):
    seed_rows = [
        TimelineScheduleSeed(
            timeline=timeline,
            franchise_id=item['franchise_id'],
            season_year=FIRST_SEASON,
            division_place=item['division_place'],
            conference_slug=item['conference_slug'],
            division_slug=item['division_slug'],
        )
        for item in seed_plan
    ]
    game_rows = [
        ScheduleGame(
            timeline=timeline,
            season_year=FIRST_SEASON,
            home_id=game.home_id,
            away_id=game.away_id,
            kind=ScheduleGame.Kind.REGULAR,
            category=game.category,
        )
        for game in expected
    ]
    try:
        with transaction.atomic():
            _clean_rows(seed_rows, 'The opening seed failed validation.')
            if seed_rows:
                TimelineScheduleSeed.objects.bulk_create(seed_rows)
            _clean_rows(game_rows, 'A fixture failed validation.')
            ScheduleGame.objects.bulk_create(game_rows)
    except IntegrityError as exc:
        raise TimelineScheduleRefused('The fixture insert failed.') from exc


def _insert_games(timeline, season_year, expected):
    game_rows = [
        ScheduleGame(
            timeline=timeline,
            season_year=season_year,
            home_id=game.home_id,
            away_id=game.away_id,
            kind=ScheduleGame.Kind.REGULAR,
            category=game.category,
        )
        for game in expected
    ]
    try:
        with transaction.atomic():
            _clean_rows(game_rows, 'A fixture failed validation.')
            ScheduleGame.objects.bulk_create(game_rows)
    except IntegrityError as exc:
        raise TimelineScheduleRefused('The fixture insert failed.') from exc


def _clean_rows(rows, message):
    try:
        for row in rows:
            row.full_clean()
    except ValidationError as exc:
        raise TimelineScheduleRefused(message) from exc


def _load_seats(release_id, season_year):
    memberships = FranchiseSeasonMembership.objects.filter(
        franchise__release_id=release_id,
        season_year=season_year,
    ).select_related('franchise')
    return [
        Seat(
            franchise_id=row.franchise_id,
            slug=row.franchise.placeholder_slug,
            conference=row.conference_slug,
            division=row.division_slug,
            slot=row.slot_order,
        )
        for row in memberships
    ]


def _groups(seats):
    groups = {}
    seen = set()
    for seat in seats:
        if seat.franchise_id in seen:
            raise TimelineScheduleRefused(SHAPE)
        seen.add(seat.franchise_id)
        if (
            seat.conference not in (AFC_SIDE, NFC_SIDE)
            or seat.division not in POSITION_INDEX
            or seat.slot not in (1, 2, 3, 4)
        ):
            raise TimelineScheduleRefused(SHAPE)
        groups.setdefault((seat.conference, seat.division), []).append(seat)
    if len(seats) != 32 or len(groups) != 8:
        raise TimelineScheduleRefused(SHAPE)
    for group in groups.values():
        if sorted(seat.slot for seat in group) != [1, 2, 3, 4]:
            raise TimelineScheduleRefused(SHAPE)
    return groups


def _rank_slugs(seed, conference, division, slugs):
    if len(slugs) != 4 or len(set(slugs)) != 4:
        raise TimelineScheduleRefused(
            'The opening place draw did not separate four clubs.'
        )
    index = {slug: 0 for slug in slugs}
    for _round in range(DRAW_LIMIT):
        scores = {
            slug: hmac_int(
                seed,
                f'opening-2001-place:{conference}:{division}:{slug}:{index[slug]}',
            )
            for slug in slugs
        }
        if len(set(scores.values())) == len(slugs):
            return tuple(sorted(slugs, key=lambda slug: scores[slug]))
        counts = Counter(scores.values())
        for slug, score in scores.items():
            if counts[score] > 1:
                index[slug] += 1
    raise TimelineScheduleRefused(
        'The opening place draw did not separate four clubs.'
    )


def _add_division_games(games, groups):
    for group in groups.values():
        ordered = sorted(group, key=lambda seat: seat.slot)
        for index, seat in enumerate(ordered):
            for other in ordered[index + 1:]:
                games.add(Fixture(
                    seat.franchise_id,
                    other.franchise_id,
                    ScheduleGame.Category.DIVISION,
                ))
                games.add(Fixture(
                    other.franchise_id,
                    seat.franchise_id,
                    ScheduleGame.Category.DIVISION,
                ))


def _add_rotating_games(games, groups, season_year):
    k3 = (season_year - FIRST_SEASON) % 3
    k4 = (season_year - FIRST_SEASON) % 4
    for conference in (AFC_SIDE, NFC_SIDE):
        for left, right in INTRA_PAIRS[k3]:
            _add_four(
                games,
                groups[(conference, POSITIONS[left])],
                groups[(conference, POSITIONS[right])],
                _flipped(season_year, FIRST_SEASON + k3, 3),
                ScheduleGame.Category.CONFERENCE,
            )
    row = INTER_ROW[k4]
    for afc_position, nfc_position in enumerate(row):
        _add_four(
            games,
            groups[(AFC_SIDE, POSITIONS[afc_position])],
            groups[(NFC_SIDE, POSITIONS[nfc_position])],
            _flipped(season_year, FIRST_SEASON + k4, 4),
            ScheduleGame.Category.INTERCONFERENCE,
        )


def _place_edges(season_year):
    """Home position, away position. ``d1`` hosts its lower opponent first."""
    k3 = (season_year - FIRST_SEASON) % 3
    partners = {}
    for left, right in INTRA_PAIRS[k3]:
        partners[left] = right
        partners[right] = left
    partner = partners[0]
    low, high = sorted(
        position for position in range(4) if position not in (0, partner)
    )
    edges = (
        (0, low),
        (high, 0),
        (low, partner),
        (partner, high),
    )
    if _flipped(season_year, FIRST_SEASON + k3, 3):
        return tuple((away, home) for home, away in edges)
    return edges


def _add_place_games(games, groups, places, season_year):
    lookup = {}
    for (conference, division), group in groups.items():
        for seat in group:
            lookup[(conference, division, places[seat.franchise_id])] = seat
    for conference in (AFC_SIDE, NFC_SIDE):
        for home_position, away_position in _place_edges(season_year):
            division = groups[(conference, POSITIONS[home_position])]
            for seat in division:
                opponent = lookup[(
                    conference,
                    POSITIONS[away_position],
                    places[seat.franchise_id],
                )]
                games.add(Fixture(
                    seat.franchise_id,
                    opponent.franchise_id,
                    ScheduleGame.Category.SAME_PLACE,
                ))


def _add_four(games, left, right, flipped, category):
    for seat in left:
        for other in right:
            if _hosts(seat.slot, other.slot, flipped):
                games.add(Fixture(seat.franchise_id, other.franchise_id, category))
            else:
                games.add(Fixture(other.franchise_id, seat.franchise_id, category))


def _hosts(slot, opponent_slot, flipped):
    same_pair = (slot <= 2) == (opponent_slot <= 2)
    if flipped:
        return not same_pair
    return same_pair


def _flipped(year, first_year, period):
    return ((year - first_year) // period) % 2 == 1


def _require_formula_year(season_year):
    if isinstance(season_year, bool) or not isinstance(season_year, int):
        raise TimelineScheduleRefused('The season is outside 2002–2020.')
    if season_year < FIRST_SEASON or season_year > LAST_SEASON:
        raise TimelineScheduleRefused('The season is outside 2002–2020.')


def _require_id(timeline_id):
    if isinstance(timeline_id, bool) or not isinstance(timeline_id, int):
        raise TimelineScheduleRefused('The timeline is missing.')
