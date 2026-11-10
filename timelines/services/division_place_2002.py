"""Close one timeline's 2002 regular season and store division places.

The stored fixture list stays as it is. This service reads those 256 games,
stores one project result for each, ranks every 2002 division, and only then
sets ``regular_season_simulated``. It does not generate 2003 fixtures, and it
does not play an overtime period.

An equal final score is a tie. A higher score is a win. Touchdown counts are
taken from the result row. The 2002–2003 Official Playing Rules were not
available here, so this slice does not apply an overtime rule. The 2003
Official Playing Rules govern the 2003 season and are not used as a 2002
source.

The division steps keep the 2002 order: head-to-head, division percentage,
common games, conference percentage, strength of victory, strength of
schedule, combined conference rank, combined league rank, net points in
common games, net points in all games, net touchdowns, then a project draw.
Clubs are ordered by exact winning percentage before those steps. The
formulas below are project rules. They are not verified 2002 NFL arithmetic.

Winning percentage is ``(wins + 0.5 × ties) / games``, compared by cross
multiplication. Common games are the opponents shared by every club still
tied for that step. There is no minimum-game count. An empty shared set does
not separate the clubs. Strength of victory and strength of schedule add each
opponent's final record once per counted appearance, including a repeat.
Combined ranks use the later published method as a project adaptation: points
scored and points allowed are ranked separately, tied values share the best
competition rank, the next rank is skipped, and the lower sum wins.

Inside one place, a step that leaves several clubs sharing the best value
drops the others and continues forward. It does not return to head-to-head.
After a place is stored, the same procedure runs again on every club that
does not yet have a place. The last remaining club is ``sole_remaining``.
Both of those repetitions are project rules. The 2002 text names the steps
and says the procedure continues until a champion is determined.
"""

import hashlib
import hmac
from collections import namedtuple
from functools import cmp_to_key

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from timelines.models import (
    DIVISION_PLACE_STEPS,
    ScheduleGame,
    ScheduleGameResult,
    Timeline,
    TimelineDivisionDraw,
    TimelineDivisionPlace,
    TimelineSeason,
    writing_division_rows,
)
from universe.generation.founding import hmac_int
from universe.models import FranchiseSeasonMembership
from universe.release_guard import assert_release_lock_held, holding_release_lock


SEASON_YEAR = 2002
DRAW_LIMIT = 8
_DRAW_SPAN = 1 << 64
_PROCEDURE = 'division-place'

HEAD_TO_HEAD = 'head_to_head'
DIVISION_PERCENTAGE = 'division_percentage'
COMMON_GAMES = 'common_games'
CONFERENCE_PERCENTAGE = 'conference_percentage'
STRENGTH_OF_VICTORY = 'strength_of_victory'
STRENGTH_OF_SCHEDULE = 'strength_of_schedule'
CONFERENCE_RANK = 'conference_rank'
LEAGUE_RANK = 'league_rank'
COMMON_NET_POINTS = 'common_net_points'
NET_POINTS = 'net_points'
NET_TOUCHDOWNS = 'net_touchdowns'
DRAW = 'draw'
SOLE_REMAINING = 'sole_remaining'
WINNING_PERCENTAGE = 'winning_percentage'

_TIEBREAK_STEPS = (
    HEAD_TO_HEAD,
    DIVISION_PERCENTAGE,
    COMMON_GAMES,
    CONFERENCE_PERCENTAGE,
    STRENGTH_OF_VICTORY,
    STRENGTH_OF_SCHEDULE,
    CONFERENCE_RANK,
    LEAGUE_RANK,
    COMMON_NET_POINTS,
    NET_POINTS,
    NET_TOUCHDOWNS,
)

Result = namedtuple(
    'Result',
    (
        'home_id',
        'away_id',
        'home_score',
        'away_score',
        'home_touchdowns',
        'away_touchdowns',
        'source_key',
    ),
)
Club = namedtuple('Club', ('key', 'conference', 'division'))
ScoredGame = namedtuple(
    'ScoredGame',
    (
        'home',
        'away',
        'home_score',
        'away_score',
        'home_touchdowns',
        'away_touchdowns',
    ),
)
PlaceChoice = namedtuple(
    'PlaceChoice',
    ('key', 'conference', 'division', 'place', 'resolved_by', 'draw_index'),
)
DrawChoice = namedtuple(
    'DrawChoice',
    (
        'conference',
        'division',
        'draw_index',
        'keys',
        'chosen_key',
        'place',
    ),
)
_Record = namedtuple('_Record', ('wins', 'losses', 'ties'))
_Appearance = namedtuple(
    '_Appearance',
    ('opponent', 'scored', 'allowed', 'touchdowns', 'allowed_touchdowns', 'result'),
)
_Bundle = namedtuple(
    '_Bundle',
    (
        'games',
        'records',
        'points_for',
        'points_allowed',
        'touchdowns_for',
        'touchdowns_allowed',
        'conference_of',
        'division_of',
        'conference_ranks',
        'league_ranks',
    ),
)


class DivisionPlaceRefused(Exception):
    """The 2002 results or division places cannot be written."""


def close_2002_regular_season(*, timeline_id, results):
    """Store 2002 results and division places. Return ``created`` or ``unchanged``.

    The release, the timeline, and the 2002 season are locked in that order.
    The stored 256 fixtures must already exist. An exact rerun writes nothing.
    A partial, mismatched, or changed result or place set is left as it is.
    """
    _require_id(timeline_id)
    probe = Timeline.objects.filter(pk=timeline_id).only('pk', 'release_id').first()
    if probe is None:
        raise DivisionPlaceRefused('The timeline is missing.')
    with transaction.atomic():
        with holding_release_lock(probe.release_id) as release:
            return _close_locked(release, timeline_id, results)


def rank_places(clubs, games, choose):
    """Return ``(places, draws)`` for every division in ``clubs``.

    ``choose(draw_index, conference, division, keys)`` returns an index into
    the sorted franchise keys when the draw step is reached. Places 1 through
    the division size each start that procedure again on the clubs that remain.
    """
    if not isinstance(clubs, (list, tuple)) or not clubs:
        raise DivisionPlaceRefused('A division place needs a club.')
    if not isinstance(games, (list, tuple)):
        raise DivisionPlaceRefused('A result needs whole-number scores and touchdowns.')
    bundle = _build_bundle(clubs, games)
    grouped = {}
    for club in clubs:
        grouped.setdefault((club.conference, club.division), []).append(club)
    places = []
    draws = []
    counters = {}
    for conference, division in sorted(grouped):
        remaining = list(grouped[(conference, division)])
        place_number = 1
        size = len(remaining)
        while remaining:
            if place_number > size:
                raise DivisionPlaceRefused('The division procedure did not finish.')
            chosen, step, draw_index = _next_club(
                remaining,
                bundle,
                choose,
                draws,
                counters,
                conference,
                division,
                place_number,
            )
            if step not in DIVISION_PLACE_STEPS:
                raise DivisionPlaceRefused(
                    'The division procedure returned an unknown step.'
                )
            places.append(PlaceChoice(
                chosen.key,
                conference,
                division,
                place_number,
                step,
                draw_index,
            ))
            remaining = [club for club in remaining if club.key != chosen.key]
            place_number += 1
    return tuple(places), tuple(draws)


def competition_rank(items, key, *, higher_better):
    """Return competition ranks aligned with ``items``.

    Tied values share the best rank. The next distinct value skips the tied
    count, so two clubs tied for first are 1 and 1 and the next club is 3.
    This is the project adaptation of the later published combined-rank method.
    """
    if isinstance(items, str) or not isinstance(items, (list, tuple)):
        raise DivisionPlaceRefused('A ranking needs the clubs being compared.')
    order = sorted(
        range(len(items)),
        key=lambda index: key(items[index]),
        reverse=higher_better,
    )
    ranks = [None] * len(items)
    position = 0
    while position < len(order):
        end = position + 1
        current = key(items[order[position]])
        while end < len(order) and key(items[order[end]]) == current:
            end += 1
        rank = position + 1
        for slot in range(position, end):
            ranks[order[slot]] = rank
        position = end
    return ranks


def division_draw_label(conference, division, keys, draw_index):
    """Coordinate prefix. The timeline id is not included."""
    joined = ','.join(keys)
    return (
        f'{_PROCEDURE}:{SEASON_YEAR}:{conference}:{division}:'
        f'{joined}:{draw_index}'
    )


def _close_locked(release, timeline_id, results):
    if release.status != release.Status.PUBLISHED:
        raise DivisionPlaceRefused('The release is not published.')
    timeline = _lock_timeline(timeline_id, release.pk)
    season = _lock_season(timeline)
    if not season.directives_locked:
        raise DivisionPlaceRefused('The 2002 directives are not locked.')
    memberships = _load_memberships(release.pk)
    games = list(
        ScheduleGame.objects.select_for_update().filter(
            timeline_id=timeline.pk,
            season_year=SEASON_YEAR,
        ).order_by('pk')
    )
    submitted = _validated_results(results, games, memberships)
    stored_results = list(
        ScheduleGameResult.objects.select_for_update().filter(
            timeline_id=timeline.pk,
            season_year=SEASON_YEAR,
        ).select_related('game')
    )
    stored_draws = list(
        TimelineDivisionDraw.objects.select_for_update().filter(
            timeline_id=timeline.pk,
            season_year=SEASON_YEAR,
        )
    )
    stored_places = list(
        TimelineDivisionPlace.objects.select_for_update().filter(
            timeline_id=timeline.pk,
            season_year=SEASON_YEAR,
        ).select_related('franchise')
    )
    stored_result_keys = {_stored_result_key(row) for row in stored_results}
    submitted_keys = {_submitted_key(row) for row in submitted}
    if stored_results:
        if stored_result_keys != submitted_keys:
            raise DivisionPlaceRefused(
                'Stored 2002 results do not match this set.'
            )
        places, draws = _rank_stored(
            release.seed,
            memberships,
            games,
            submitted,
            stored_draws,
        )
        if not _places_match(stored_places, stored_draws, places, draws):
            raise DivisionPlaceRefused(
                'Stored 2002 division places do not match this set.'
            )
        if not season.regular_season_simulated:
            raise DivisionPlaceRefused(
                'Stored 2002 results do not match this set.'
            )
        return 'unchanged'
    if stored_places or stored_draws or season.regular_season_simulated:
        raise DivisionPlaceRefused('Stored 2002 results do not match this set.')
    places, draws, pending = _rank_new(
        release.seed,
        memberships,
        games,
        submitted,
    )
    _require_place_shape(memberships, places, draws)
    assert_release_lock_held(release.pk)
    _insert_season(timeline, season, games, submitted, memberships, places, draws, pending)
    return 'created'


def _rank_new(seed, memberships, games, submitted):
    pending = []

    def choose(draw_index, conference, division, keys):
        label = division_draw_label(conference, division, keys, draw_index)
        message, digest, choice = _resolving_draw(seed, label, len(keys))
        pending.append((message, digest, choice))
        return choice

    clubs, scored = _views(memberships, games, submitted)
    places, draws = rank_places(clubs, scored, choose)
    if len(pending) != len(draws):
        raise DivisionPlaceRefused('The division draw did not resolve.')
    for draw, (message, digest, choice) in zip(draws, pending):
        if draw.keys[choice] != draw.chosen_key:
            raise DivisionPlaceRefused('The division draw did not resolve.')
        if digest != _hex_digest(seed, message):
            raise DivisionPlaceRefused('The division draw did not resolve.')
    return places, draws, pending


def _rank_stored(seed, memberships, games, submitted, stored_draws):
    by_index = {
        (row.conference_slug, row.division_slug, row.draw_index): row
        for row in stored_draws
    }
    seen = set()

    def choose(draw_index, conference, division, keys):
        row = by_index.get((conference, division, draw_index))
        if row is None:
            raise DivisionPlaceRefused('A division draw is missing.')
        label = division_draw_label(conference, division, keys, draw_index)
        prefix = f'{label}:'
        if not row.coordinate.startswith(prefix):
            raise DivisionPlaceRefused(
                'A stored division draw does not match this season.'
            )
        attempt = row.coordinate[len(prefix):]
        if not attempt.isdigit():
            raise DivisionPlaceRefused(
                'A stored division draw does not match this season.'
            )
        digest = _hex_digest(seed, row.coordinate)
        if digest != row.digest:
            raise DivisionPlaceRefused(
                'A stored division draw does not match this season.'
            )
        choice = _index_from_digest(digest, len(keys))
        if choice is None or keys[choice] != row.chosen_slug:
            raise DivisionPlaceRefused(
                'A stored division draw does not match this season.'
            )
        seen.add((conference, division, draw_index))
        return choice

    clubs, scored = _views(memberships, games, submitted)
    places, draws = rank_places(clubs, scored, choose)
    if seen != set(by_index):
        raise DivisionPlaceRefused(
            'A stored division draw does not match this season.'
        )
    return places, draws


def _next_club(
    remaining,
    bundle,
    choose,
    draws,
    counters,
    conference,
    division,
    place_number,
):
    if len(remaining) == 1:
        return remaining[0], SOLE_REMAINING, None
    best = _best_clubs(
        remaining,
        lambda club: _overall_fraction(bundle, club.key),
    )
    if len(best) == 1:
        return best[0], WINNING_PERCENTAGE, None
    return _tiebreak(
        best,
        bundle,
        choose,
        draws,
        counters,
        conference,
        division,
        place_number,
    )


def _tiebreak(
    group,
    bundle,
    choose,
    draws,
    counters,
    conference,
    division,
    place_number,
):
    """Walk the 2002 steps forward on the clubs tied for this place."""
    current = list(group)
    step_index = 0
    while len(current) > 1 and step_index < len(_TIEBREAK_STEPS):
        step = _TIEBREAK_STEPS[step_index]
        values = _step_values(step, current, bundle)
        kept = _best_clubs(current, lambda club: values[club.key])
        if len(kept) == len(current):
            step_index += 1
            continue
        if len(kept) == 1:
            return kept[0], step, None
        current = kept
        step_index += 1
    if len(current) == 1:
        return current[0], SOLE_REMAINING, None
    return _project_draw(
        current,
        choose,
        draws,
        counters,
        conference,
        division,
        place_number,
    )


def _project_draw(
    current,
    choose,
    draws,
    counters,
    conference,
    division,
    place_number,
):
    keys = tuple(sorted(club.key for club in current))
    marker = (conference, division)
    draw_index = counters.get(marker, 0)
    counters[marker] = draw_index + 1
    choice = choose(draw_index, conference, division, keys)
    if (
        type(choice) is not int
        or isinstance(choice, bool)
        or choice < 0
        or choice >= len(keys)
    ):
        raise DivisionPlaceRefused('The division draw did not resolve.')
    chosen_key = keys[choice]
    draws.append(DrawChoice(
        conference,
        division,
        draw_index,
        keys,
        chosen_key,
        place_number,
    ))
    chosen = next(club for club in current if club.key == chosen_key)
    return chosen, DRAW, draw_index


def _step_values(step, current, bundle):
    if step == HEAD_TO_HEAD:
        return {
            club.key: _group_fraction(club.key, current, bundle)
            for club in current
        }
    if step == DIVISION_PERCENTAGE:
        return {
            club.key: _filtered_fraction(
                club.key,
                bundle,
                lambda club_key, game: (
                    bundle.division_of[game.opponent] == bundle.division_of[club_key]
                    and bundle.conference_of[game.opponent]
                    == bundle.conference_of[club_key]
                ),
            )
            for club in current
        }
    if step == COMMON_GAMES:
        shared = _shared_opponents(current, bundle)
        if not shared:
            return {club.key: ('missing',) for club in current}
        return {
            club.key: _filtered_fraction(
                club.key,
                bundle,
                lambda _club_key, game, shared=shared: game.opponent in shared,
            )
            for club in current
        }
    if step == CONFERENCE_PERCENTAGE:
        return {
            club.key: _filtered_fraction(
                club.key,
                bundle,
                lambda club_key, game: (
                    bundle.conference_of[game.opponent]
                    == bundle.conference_of[club_key]
                ),
            )
            for club in current
        }
    if step == STRENGTH_OF_VICTORY:
        return {
            club.key: _opponent_fraction(club.key, bundle, wins_only=True)
            for club in current
        }
    if step == STRENGTH_OF_SCHEDULE:
        return {
            club.key: _opponent_fraction(club.key, bundle, wins_only=False)
            for club in current
        }
    if step == CONFERENCE_RANK:
        return {
            club.key: ('low', bundle.conference_ranks[club.key])
            for club in current
        }
    if step == LEAGUE_RANK:
        return {
            club.key: ('low', bundle.league_ranks[club.key])
            for club in current
        }
    if step == COMMON_NET_POINTS:
        shared = _shared_opponents(current, bundle)
        if not shared:
            return {club.key: ('missing',) for club in current}
        return {
            club.key: (
                'high',
                sum(
                    game.scored - game.allowed
                    for game in bundle.games[club.key]
                    if game.opponent in shared
                ),
            )
            for club in current
        }
    if step == NET_POINTS:
        return {
            club.key: (
                'high',
                bundle.points_for[club.key] - bundle.points_allowed[club.key],
            )
            for club in current
        }
    if step == NET_TOUCHDOWNS:
        return {
            club.key: (
                'high',
                bundle.touchdowns_for[club.key]
                - bundle.touchdowns_allowed[club.key],
            )
            for club in current
        }
    raise DivisionPlaceRefused('The division procedure returned an unknown step.')


def _shared_opponents(current, bundle):
    keys = {club.key for club in current}
    shared = None
    for club in current:
        opponents = {
            game.opponent
            for game in bundle.games[club.key]
            if game.opponent not in keys
        }
        shared = opponents if shared is None else shared & opponents
    return shared or set()


def _group_fraction(club_key, current, bundle):
    keys = {club.key for club in current}
    return _filtered_fraction(
        club_key,
        bundle,
        lambda _club_key, game: game.opponent in keys,
    )


def _filtered_fraction(club_key, bundle, predicate):
    wins = losses = ties = 0
    for game in bundle.games[club_key]:
        if not predicate(club_key, game):
            continue
        if game.result == 'win':
            wins += 1
        elif game.result == 'loss':
            losses += 1
        else:
            ties += 1
    return _fraction(wins, losses, ties)


def _opponent_fraction(club_key, bundle, *, wins_only):
    numerator = 0
    denominator = 0
    counted = 0
    for game in bundle.games[club_key]:
        if wins_only and game.result != 'win':
            continue
        opponent = bundle.records[game.opponent]
        numerator += 2 * opponent.wins + opponent.ties
        denominator += 2 * (opponent.wins + opponent.losses + opponent.ties)
        counted += 1
    if counted == 0 or denominator == 0:
        return ('missing',)
    return ('fraction', numerator, denominator)


def _overall_fraction(bundle, club_key):
    record = bundle.records[club_key]
    return _fraction(record.wins, record.losses, record.ties)


def _fraction(wins, losses, ties):
    games = wins + losses + ties
    if games == 0:
        return ('missing',)
    return ('fraction', 2 * wins + ties, 2 * games)


def _best_clubs(clubs, value):
    ordered = sorted(clubs, key=cmp_to_key(lambda left, right: _cmp_better(
        value(left),
        value(right),
    )))
    top = value(ordered[0])
    return [club for club in ordered if _cmp_better(value(club), top) == 0]


def _cmp_better(left, right):
    """Return negative when ``left`` is better, positive when ``right`` is."""
    if left[0] == 'missing' and right[0] == 'missing':
        return 0
    if left[0] == 'missing':
        return 1
    if right[0] == 'missing':
        return -1
    if left[0] != right[0]:
        raise DivisionPlaceRefused('Tiebreak values cannot be compared.')
    if left[0] == 'fraction':
        left_cross = left[1] * right[2]
        right_cross = right[1] * left[2]
        if left_cross > right_cross:
            return -1
        if left_cross < right_cross:
            return 1
        return 0
    if left[0] == 'high':
        if left[1] > right[1]:
            return -1
        if left[1] < right[1]:
            return 1
        return 0
    if left[0] == 'low':
        if left[1] < right[1]:
            return -1
        if left[1] > right[1]:
            return 1
        return 0
    raise DivisionPlaceRefused('Tiebreak values cannot be compared.')


def _build_bundle(clubs, games):
    by_key = {}
    conference_of = {}
    division_of = {}
    for club in clubs:
        if (
            not isinstance(club, Club)
            or not isinstance(club.key, str)
            or club.key == ''
            or not isinstance(club.conference, str)
            or club.conference == ''
            or not isinstance(club.division, str)
            or club.division == ''
        ):
            raise DivisionPlaceRefused('A division place needs a club.')
        if club.key in by_key:
            raise DivisionPlaceRefused('A division place repeats a club.')
        by_key[club.key] = club
        conference_of[club.key] = club.conference
        division_of[club.key] = club.division
    appearances = {key: [] for key in by_key}
    for game in games:
        _require_game(game, by_key)
        _append_appearance(
            appearances,
            game.home,
            game.away,
            game.home_score,
            game.away_score,
            game.home_touchdowns,
            game.away_touchdowns,
        )
        _append_appearance(
            appearances,
            game.away,
            game.home,
            game.away_score,
            game.home_score,
            game.away_touchdowns,
            game.home_touchdowns,
        )
    records = {}
    points_for = {}
    points_allowed = {}
    touchdowns_for = {}
    touchdowns_allowed = {}
    for key, rows in appearances.items():
        wins = losses = ties = 0
        scored = allowed = 0
        scored_touchdowns = allowed_touchdowns = 0
        for row in rows:
            if row.result == 'win':
                wins += 1
            elif row.result == 'loss':
                losses += 1
            else:
                ties += 1
            scored += row.scored
            allowed += row.allowed
            scored_touchdowns += row.touchdowns
            allowed_touchdowns += row.allowed_touchdowns
        records[key] = _Record(wins, losses, ties)
        points_for[key] = scored
        points_allowed[key] = allowed
        touchdowns_for[key] = scored_touchdowns
        touchdowns_allowed[key] = allowed_touchdowns
    conference_ranks = {}
    by_conference = {}
    for club in clubs:
        by_conference.setdefault(club.conference, []).append(club)
    for members in by_conference.values():
        conference_ranks.update(_combined_sums(members, points_for, points_allowed))
    league_ranks = _combined_sums(list(clubs), points_for, points_allowed)
    return _Bundle(
        appearances,
        records,
        points_for,
        points_allowed,
        touchdowns_for,
        touchdowns_allowed,
        conference_of,
        division_of,
        conference_ranks,
        league_ranks,
    )


def _combined_sums(members, points_for, points_allowed):
    scored = competition_rank(
        members,
        key=lambda club: points_for[club.key],
        higher_better=True,
    )
    allowed = competition_rank(
        members,
        key=lambda club: points_allowed[club.key],
        higher_better=False,
    )
    return {
        club.key: scored[index] + allowed[index]
        for index, club in enumerate(members)
    }


def _require_game(game, by_key):
    if not isinstance(game, ScoredGame):
        raise DivisionPlaceRefused(
            'A result needs whole-number scores and touchdowns.'
        )
    if game.home not in by_key or game.away not in by_key or game.home == game.away:
        raise DivisionPlaceRefused('A result uses seated clubs.')
    for value in (
        game.home_score,
        game.away_score,
        game.home_touchdowns,
        game.away_touchdowns,
    ):
        _require_count(value)


def _append_appearance(appearances, club_key, opponent, scored, allowed, scored_td, allowed_td):
    if scored > allowed:
        result = 'win'
    elif scored < allowed:
        result = 'loss'
    else:
        result = 'tie'
    appearances[club_key].append(_Appearance(
        opponent,
        scored,
        allowed,
        scored_td,
        allowed_td,
        result,
    ))


def _views(memberships, games, submitted):
    by_pair = {(row.home_id, row.away_id): row for row in submitted}
    slug_of = {}
    clubs = []
    for row in memberships:
        slug = row.franchise.placeholder_slug
        if not slug or slug in slug_of.values():
            raise DivisionPlaceRefused('The 2002 alignment is not eight divisions of four.')
        slug_of[row.franchise_id] = slug
        clubs.append(Club(slug, row.conference_slug, row.division_slug))
    scored = []
    for game in games:
        row = by_pair[(game.home_id, game.away_id)]
        scored.append(ScoredGame(
            slug_of[game.home_id],
            slug_of[game.away_id],
            row.home_score,
            row.away_score,
            row.home_touchdowns,
            row.away_touchdowns,
        ))
    return clubs, scored


def _validated_results(results, games, memberships):
    if not isinstance(results, (list, tuple)) or isinstance(results, str):
        raise DivisionPlaceRefused('The 2002 results do not match the stored fixtures.')
    if len(games) != 256:
        raise DivisionPlaceRefused('The 2002 fixtures are incomplete.')
    _require_alignment(memberships)
    member_ids = {row.franchise_id for row in memberships}
    fixture_pairs = set()
    appearances = {franchise_id: 0 for franchise_id in member_ids}
    for game in games:
        if game.season_year != SEASON_YEAR or game.kind != ScheduleGame.Kind.REGULAR:
            raise DivisionPlaceRefused('The 2002 fixtures are incomplete.')
        if (
            game.home_id == game.away_id
            or game.home_id not in member_ids
            or game.away_id not in member_ids
        ):
            raise DivisionPlaceRefused('The 2002 fixtures are incomplete.')
        pair = (game.home_id, game.away_id)
        if pair in fixture_pairs:
            raise DivisionPlaceRefused('The 2002 fixtures are incomplete.')
        fixture_pairs.add(pair)
        appearances[game.home_id] += 1
        appearances[game.away_id] += 1
    if any(count != 16 for count in appearances.values()):
        raise DivisionPlaceRefused('The 2002 fixtures are incomplete.')
    if len(results) != 256:
        raise DivisionPlaceRefused('The 2002 results do not match the stored fixtures.')
    normalized = []
    seen = set()
    for result in results:
        if not isinstance(result, Result):
            raise DivisionPlaceRefused(
                'A result needs whole-number scores and touchdowns.'
            )
        home_id = _require_identity(result.home_id)
        away_id = _require_identity(result.away_id)
        pair = (home_id, away_id)
        if pair not in fixture_pairs or pair in seen:
            raise DivisionPlaceRefused(
                'The 2002 results do not match the stored fixtures.'
            )
        seen.add(pair)
        normalized.append(Result(
            home_id,
            away_id,
            _require_count(result.home_score),
            _require_count(result.away_score),
            _require_count(result.home_touchdowns),
            _require_count(result.away_touchdowns),
            _require_source(result.source_key),
        ))
    if seen != fixture_pairs:
        raise DivisionPlaceRefused('The 2002 results do not match the stored fixtures.')
    return normalized


def _require_alignment(memberships):
    if len(memberships) != 32:
        raise DivisionPlaceRefused('The 2002 alignment is not eight divisions of four.')
    groups = {}
    seen = set()
    for row in memberships:
        if row.franchise_id in seen:
            raise DivisionPlaceRefused(
                'The 2002 alignment is not eight divisions of four.'
            )
        seen.add(row.franchise_id)
        if (
            row.conference_slug not in ('conference-1', 'conference-2')
            or row.division_slug not in ('d1', 'd2', 'd3', 'd4')
        ):
            raise DivisionPlaceRefused(
                'The 2002 alignment is not eight divisions of four.'
            )
        groups.setdefault((row.conference_slug, row.division_slug), []).append(row)
    if len(groups) != 8 or any(len(members) != 4 for members in groups.values()):
        raise DivisionPlaceRefused('The 2002 alignment is not eight divisions of four.')


def _require_place_shape(memberships, places, draws):
    if len(places) != 32:
        raise DivisionPlaceRefused('The division procedure did not finish.')
    by_slug = {row.franchise.placeholder_slug: row for row in memberships}
    grouped = {}
    for place in places:
        if place.key not in by_slug:
            raise DivisionPlaceRefused('The division procedure did not finish.')
        membership = by_slug[place.key]
        if (
            place.conference != membership.conference_slug
            or place.division != membership.division_slug
            or place.resolved_by not in DIVISION_PLACE_STEPS
        ):
            raise DivisionPlaceRefused('The division procedure did not finish.')
        grouped.setdefault((place.conference, place.division), []).append(place)
    if len(grouped) != 8:
        raise DivisionPlaceRefused('The division procedure did not finish.')
    draw_keys = {
        (draw.conference, draw.division, draw.draw_index)
        for draw in draws
    }
    if len(draw_keys) != len(draws):
        raise DivisionPlaceRefused('The division draw did not resolve.')
    for members in grouped.values():
        found = sorted(place.place for place in members)
        if found != [1, 2, 3, 4]:
            raise DivisionPlaceRefused('The division procedure did not finish.')
        for place in members:
            if place.resolved_by == DRAW:
                if (place.conference, place.division, place.draw_index) not in draw_keys:
                    raise DivisionPlaceRefused('The division draw did not resolve.')
            elif place.draw_index is not None:
                raise DivisionPlaceRefused('The division procedure did not finish.')


def _places_match(stored_places, stored_draws, places, draws):
    if len(stored_places) != len(places) or len(stored_draws) != len(draws):
        return False
    draw_by_index = {
        (row.conference_slug, row.division_slug, row.draw_index): row
        for row in stored_draws
    }
    expected_draws = {}
    for draw in draws:
        expected_draws[(draw.conference, draw.division, draw.draw_index)] = draw
        row = draw_by_index.get((draw.conference, draw.division, draw.draw_index))
        if row is None:
            return False
        if (
            row.candidates != ','.join(draw.keys)
            or row.chosen_slug != draw.chosen_key
            or row.outcome != f'{draw.place}:{draw.chosen_key}'
        ):
            return False
    if set(draw_by_index) != set(expected_draws):
        return False
    stored_by_slug = {}
    for row in stored_places:
        stored_by_slug[row.franchise.placeholder_slug] = row
    if set(stored_by_slug) != {place.key for place in places}:
        return False
    for place in places:
        row = stored_by_slug[place.key]
        if (
            row.division_place != place.place
            or row.conference_slug != place.conference
            or row.division_slug != place.division
            or row.resolved_by != place.resolved_by
        ):
            return False
        if place.resolved_by == DRAW:
            draw = draw_by_index[(place.conference, place.division, place.draw_index)]
            if row.draw_id != draw.pk:
                return False
        elif row.draw_id is not None:
            return False
    return True


def _insert_season(timeline, season, games, submitted, memberships, places, draws, pending):
    by_pair = {(row.home_id, row.away_id): row for row in submitted}
    result_rows = []
    for game in games:
        row = by_pair[(game.home_id, game.away_id)]
        result_rows.append(ScheduleGameResult(
            timeline=timeline,
            game=game,
            season_year=SEASON_YEAR,
            home_score=row.home_score,
            away_score=row.away_score,
            home_touchdowns=row.home_touchdowns,
            away_touchdowns=row.away_touchdowns,
            source_key=row.source_key,
        ))
    draw_rows = []
    for draw, (message, digest, _choice) in zip(draws, pending):
        draw_rows.append(TimelineDivisionDraw(
            timeline=timeline,
            season_year=SEASON_YEAR,
            conference_slug=draw.conference,
            division_slug=draw.division,
            draw_index=draw.draw_index,
            candidates=','.join(draw.keys),
            coordinate=message,
            digest=digest,
            chosen_slug=draw.chosen_key,
            outcome=f'{draw.place}:{draw.chosen_key}',
        ))
    by_slug = {row.franchise.placeholder_slug: row for row in memberships}
    try:
        with transaction.atomic():
            with writing_division_rows():
                _clean_rows(result_rows, 'A 2002 result failed validation.')
                ScheduleGameResult.objects.bulk_create(result_rows)
                _clean_rows(draw_rows, 'A division draw failed validation.')
                if draw_rows:
                    TimelineDivisionDraw.objects.bulk_create(draw_rows)
                draw_ids = {
                    (row.conference_slug, row.division_slug, row.draw_index): row.pk
                    for row in TimelineDivisionDraw.objects.filter(
                        timeline=timeline,
                        season_year=SEASON_YEAR,
                    )
                }
                if len(draw_ids) != len(draws):
                    raise DivisionPlaceRefused('The division draw insert failed.')
                place_rows = []
                for place in places:
                    membership = by_slug[place.key]
                    draw_id = None
                    if place.resolved_by == DRAW:
                        draw_id = draw_ids[(place.conference, place.division, place.draw_index)]
                    place_rows.append(TimelineDivisionPlace(
                        timeline=timeline,
                        franchise_id=membership.franchise_id,
                        season_year=SEASON_YEAR,
                        division_place=place.place,
                        conference_slug=place.conference,
                        division_slug=place.division,
                        resolved_by=place.resolved_by,
                        draw_id=draw_id,
                    ))
                _clean_rows(place_rows, 'A 2002 division place failed validation.')
                TimelineDivisionPlace.objects.bulk_create(place_rows)
                season.regular_season_simulated = True
                season.save(update_fields=['regular_season_simulated'])
    except IntegrityError as exc:
        raise DivisionPlaceRefused('The 2002 division insert failed.') from exc
    except ValidationError as exc:
        raise DivisionPlaceRefused('The 2002 season cannot be marked complete.') from exc


def _clean_rows(rows, message):
    try:
        for row in rows:
            row.full_clean()
    except ValidationError as exc:
        raise DivisionPlaceRefused(message) from exc


def _load_memberships(release_id):
    return list(
        FranchiseSeasonMembership.objects.filter(
            franchise__release_id=release_id,
            season_year=SEASON_YEAR,
        ).select_related('franchise')
    )


def _stored_result_key(row):
    return (
        row.game.home_id,
        row.game.away_id,
        row.home_score,
        row.away_score,
        row.home_touchdowns,
        row.away_touchdowns,
        row.source_key,
    )


def _submitted_key(row):
    return (
        row.home_id,
        row.away_id,
        row.home_score,
        row.away_score,
        row.home_touchdowns,
        row.away_touchdowns,
        row.source_key,
    )


def _resolving_draw(seed, label, modulus, draw=hmac_int):
    if modulus <= 1:
        message = f'{label}:0'
        return message, _hex_digest(seed, message), 0
    limit = (_DRAW_SPAN // modulus) * modulus
    for attempt in range(DRAW_LIMIT):
        message = f'{label}:{attempt}'
        value = draw(seed, message)
        if type(value) is not int or isinstance(value, bool) or value < 0 or value >= limit:
            continue
        return message, _hex_digest(seed, message), value % modulus
    raise DivisionPlaceRefused('The division draw did not resolve.')


def _hex_digest(seed, message):
    return hmac.new(
        str(seed).encode('utf-8'),
        str(message).encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()


def _index_from_digest(digest_hex, modulus):
    if modulus <= 1:
        return 0
    value = int(digest_hex[:16], 16)
    limit = (_DRAW_SPAN // modulus) * modulus
    if value >= limit:
        return None
    return value % modulus


def _require_count(value):
    if (
        type(value) is not int
        or isinstance(value, bool)
        or value < 0
        or value > 32767
    ):
        raise DivisionPlaceRefused(
            'A result needs whole-number scores and touchdowns.'
        )
    return value


def _require_identity(value):
    if type(value) is not int or isinstance(value, bool):
        raise DivisionPlaceRefused('The 2002 results do not match the stored fixtures.')
    return value


def _require_source(value):
    if not isinstance(value, str) or value == '' or len(value) > 64:
        raise DivisionPlaceRefused('A result names its producing source.')
    return value


def _require_id(timeline_id):
    if isinstance(timeline_id, bool) or not isinstance(timeline_id, int):
        raise DivisionPlaceRefused('The timeline is missing.')


def _lock_timeline(timeline_id, release_id):
    try:
        timeline = Timeline.objects.select_for_update().get(pk=timeline_id)
    except Timeline.DoesNotExist as exc:
        raise DivisionPlaceRefused('The timeline is missing.') from exc
    if timeline.release_id != release_id:
        raise DivisionPlaceRefused('The timeline is missing.')
    if timeline.status != Timeline.Status.ACTIVE:
        raise DivisionPlaceRefused('The timeline is not active.')
    return timeline


def _lock_season(timeline):
    try:
        season = TimelineSeason.objects.select_for_update().get(
            timeline_id=timeline.pk,
            season_year=SEASON_YEAR,
        )
    except TimelineSeason.DoesNotExist as exc:
        raise DivisionPlaceRefused('The timeline has no 2002 season.') from exc
    return season
