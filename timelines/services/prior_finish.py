"""2001 division finishes and the derived 2002 scheduling ranks.

Records are accepted only after the verified-result service has completed
the 2001 season by setting ``regular_season_simulated``. This module does
not implement results, and it does not set that flag.

A 2001 place stays on the stored 2001 division. Winning percentage orders
the clubs that played. A tie inside that division keeps the resolved place
order. Equal percentages may use either place order because the historical
2001 tiebreak service is outside this slice. A strictly better percentage
needs the better place. A club with no 2001 season ranks after every club
that played. Clubs from different 2001 divisions with the same percentage
are ordered by a scheduling draw that preserves each division's place order.

Each resolving draw is stored on the timeline with the ranks, in one
transaction. The coordinate is HMAC-SHA256 of the release seed. The
timeline id is not an input, so identical inputs under one release produce
the same transition ranks. An exact rerun reads the stored draw and does
not sample another one.
"""

import hashlib
import hmac
from collections import namedtuple
from functools import cmp_to_key

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from timelines.models import (
    Timeline,
    TimelinePriorFinish,
    TimelineSchedulingDraw,
    TimelineSchedulingRank,
    TimelineSeason,
    writing_transition_rows,
)
from universe.generation.founding import hmac_int
from universe.models import FranchiseSeasonMembership
from universe.release_guard import assert_release_lock_held, holding_release_lock


PRIOR_YEAR = 2001
RANK_YEAR = 2002
DRAW_LIMIT = 8
_DRAW_SPAN = 1 << 64

Finish = namedtuple(
    'Finish',
    ('franchise_id', 'wins', 'losses', 'ties', 'division_place'),
)
PriorClub = namedtuple(
    'PriorClub',
    ('slug', 'wins', 'losses', 'ties', 'conference', 'division', 'place'),
)


class PriorFinishRefused(Exception):
    """The 2001 finishes or the 2002 scheduling ranks cannot be written."""


def store_prior_finishes(*, timeline_id, finishes):
    """Store one complete played 2001 finish set. Return ``created`` or ``unchanged``.

    The release, the timeline, and the 2001 season are locked in that order.
    The season must already have ``regular_season_simulated`` set. Every
    club's wins, losses, and ties must add up to that season's stored
    ``schedule_length``.
    """
    _require_id(timeline_id)
    probe = Timeline.objects.filter(pk=timeline_id).only('pk', 'release_id').first()
    if probe is None:
        raise PriorFinishRefused('The timeline is missing.')
    with transaction.atomic():
        with holding_release_lock(probe.release_id) as release:
            return _store_locked(release, timeline_id, finishes)


def derive_scheduling_ranks(*, timeline_id):
    """Store the 2002 scheduling ranks derived from a complete 2001 finish set.

    The 2001 season and the prior-finish rows are locked. Draw rows and
    ranks are written in one transaction. An exact rerun checks the stored
    draw and writes nothing.
    """
    _require_id(timeline_id)
    probe = Timeline.objects.filter(pk=timeline_id).only('pk', 'release_id').first()
    if probe is None:
        raise PriorFinishRefused('The timeline is missing.')
    with transaction.atomic():
        with holding_release_lock(probe.release_id) as release:
            return _derive_locked(release, timeline_id)


def scheduling_order(clubs, choose, record=None):
    """Return slugs in scheduling-rank order.

    ``choose(step, remaining_slugs, modulus)`` supplies the draw. It is
    called only when at least two 2001 divisions still have a club in a
    tied percentage group, or when more than one club has no 2001 season.
    ``record`` receives each resolving step after the choice is applied.
    """
    played, unplayed = _split_clubs(clubs)
    ordered = []
    step = 0
    for group in _percentage_groups(played):
        step = _extend(ordered, _chains(group, played=True), choose, step, record)
    if unplayed:
        step = _extend(ordered, _chains(unplayed, played=False), choose, step, record)
    if len(ordered) != len(clubs):
        raise PriorFinishRefused('The scheduling order did not rank every club.')
    return tuple(ordered)


def _store_locked(release, timeline_id, finishes):
    _require_published(release)
    timeline = _lock_timeline(timeline_id, release.pk)
    season = _lock_prior_season(timeline)
    plan = _finish_plan(
        release.pk,
        finishes,
        season.profile.schedule_length,
    )
    stored = list(
        TimelinePriorFinish.objects.select_for_update().filter(
            timeline_id=timeline.pk,
            season_year=PRIOR_YEAR,
        ).order_by('pk')
    )
    found = {_finish_key(row) for row in stored}
    if found == {_finish_plan_key(item) for item in plan}:
        return 'unchanged'
    if found:
        raise PriorFinishRefused('Stored 2001 finishes do not match this set.')
    assert_release_lock_held(release.pk)
    rows = [
        TimelinePriorFinish(
            timeline=timeline,
            franchise_id=item['franchise_id'],
            season_year=PRIOR_YEAR,
            wins=item['wins'],
            losses=item['losses'],
            ties=item['ties'],
            division_place=item['division_place'],
            conference_slug=item['conference_slug'],
            division_slug=item['division_slug'],
        )
        for item in plan
    ]
    _insert_finishes(rows)
    return 'created'


def _derive_locked(release, timeline_id):
    _require_published(release)
    timeline = _lock_timeline(timeline_id, release.pk)
    season = _lock_prior_season(timeline)
    finishes = list(
        TimelinePriorFinish.objects.select_for_update().filter(
            timeline_id=timeline.pk,
            season_year=PRIOR_YEAR,
        )
    )
    if not finishes:
        raise PriorFinishRefused('2001 finishes are not stored.')
    _require_consistent(
        release.pk,
        finishes,
        season.profile.schedule_length,
    )
    prior_ids = set(_memberships(release.pk, PRIOR_YEAR))
    current = _seats(release.pk, RANK_YEAR)
    current_ids = {seat['franchise_id'] for seat in current}
    if not prior_ids <= current_ids:
        raise PriorFinishRefused('A 2001 finish has no 2002 seat.')
    stored_draws = list(
        TimelineSchedulingDraw.objects.select_for_update().filter(
            timeline_id=timeline.pk,
            season_year=RANK_YEAR,
        )
    )
    stored_ranks = list(
        TimelineSchedulingRank.objects.select_for_update().filter(
            timeline_id=timeline.pk,
            season_year=RANK_YEAR,
        ).order_by('pk')
    )
    creating = not stored_draws and not stored_ranks
    groups = _groups(current)
    by_franchise = {row.franchise_id: row for row in finishes}
    plan = []
    planned_draws = []
    visited = set()
    for (conference, division), seats in sorted(groups.items()):
        clubs = []
        for seat in seats:
            row = by_franchise.get(seat['franchise_id'])
            if seat['franchise_id'] not in prior_ids:
                clubs.append(PriorClub(
                    seat['slug'], None, None, None, None, None, None,
                ))
                continue
            if row is None:
                raise PriorFinishRefused(
                    'A 2002 club with a 2001 membership has no finish.'
                )
            clubs.append(PriorClub(
                seat['slug'],
                row.wins,
                row.losses,
                row.ties,
                row.conference_slug,
                row.division_slug,
                row.division_place,
            ))
        division_draws = {
            row.draw_step: row
            for row in stored_draws
            if row.conference_slug == conference and row.division_slug == division
        }
        pending = {}
        local_visited = set()

        def choose(
            step,
            slugs,
            modulus,
            _conference=conference,
            _division=division,
            _draws=division_draws,
            _pending=pending,
        ):
            label = (
                f'scheduling-rank-2002:{_conference}:{_division}:'
                f'{step}:{",".join(slugs)}'
            )
            if creating:
                message, digest, choice = _resolving_draw(
                    release.seed,
                    label,
                    modulus,
                )
                _pending[step] = (message, digest)
                return choice
            row = _draws.get(step)
            if row is None:
                raise PriorFinishRefused('A scheduling draw is missing.')
            prefix = f'{label}:'
            attempt = row.coordinate[len(prefix):] if row.coordinate.startswith(prefix) else ''
            if not attempt.isdigit():
                raise PriorFinishRefused(
                    'A stored scheduling draw does not match this season.'
                )
            digest = _hex_digest(release.seed, row.coordinate)
            if digest != row.digest:
                raise PriorFinishRefused(
                    'A stored scheduling draw does not match this season.'
                )
            choice = _index_from_digest(digest, modulus)
            if choice is None:
                raise PriorFinishRefused(
                    'A stored scheduling draw does not match this season.'
                )
            return choice

        def record(
            step,
            _slugs,
            candidates,
            chosen_slug,
            chain_name,
            outcome,
            _conference=conference,
            _division=division,
            _draws=division_draws,
            _pending=pending,
        ):
            local_visited.add(step)
            visited.add((_conference, _division, step))
            outcome_text = ','.join(outcome)
            if creating:
                message, digest = _pending[step]
                planned_draws.append({
                    'conference_slug': _conference,
                    'division_slug': _division,
                    'draw_step': step,
                    'candidates': candidates,
                    'coordinate': message,
                    'digest': digest,
                    'chosen_slug': chosen_slug,
                    'chosen_chain': chain_name,
                    'outcome': outcome_text,
                })
                return
            row = _draws[step]
            if (
                row.candidates != candidates
                or row.chosen_slug != chosen_slug
                or row.chosen_chain != chain_name
                or row.outcome != outcome_text
            ):
                raise PriorFinishRefused(
                    'A stored scheduling draw does not match this season.'
                )

        ordered = scheduling_order(clubs, choose, record)
        if not creating and set(division_draws) != local_visited:
            raise PriorFinishRefused(
                'A stored scheduling draw does not match this season.'
            )
        by_slug = {seat['slug']: seat for seat in seats}
        for rank, slug in enumerate(ordered, start=1):
            seat = by_slug[slug]
            plan.append({
                'franchise_id': seat['franchise_id'],
                'scheduling_rank': rank,
                'conference_slug': conference,
                'division_slug': division,
            })
    if not creating:
        stored_keys = {
            (row.conference_slug, row.division_slug, row.draw_step)
            for row in stored_draws
        }
        if stored_keys != visited:
            raise PriorFinishRefused(
                'A stored scheduling draw does not match this season.'
            )
        found = {_rank_key(row) for row in stored_ranks}
        if found != {_rank_plan_key(item) for item in plan}:
            raise PriorFinishRefused(
                'Stored scheduling ranks do not match this derivation.'
            )
        return 'unchanged'
    assert_release_lock_held(release.pk)
    draw_rows = [
        TimelineSchedulingDraw(
            timeline=timeline,
            season_year=RANK_YEAR,
            conference_slug=item['conference_slug'],
            division_slug=item['division_slug'],
            draw_step=item['draw_step'],
            candidates=item['candidates'],
            coordinate=item['coordinate'],
            digest=item['digest'],
            chosen_slug=item['chosen_slug'],
            chosen_chain=item['chosen_chain'],
            outcome=item['outcome'],
        )
        for item in planned_draws
    ]
    rank_rows = [
        TimelineSchedulingRank(
            timeline=timeline,
            franchise_id=item['franchise_id'],
            season_year=RANK_YEAR,
            scheduling_rank=item['scheduling_rank'],
            conference_slug=item['conference_slug'],
            division_slug=item['division_slug'],
        )
        for item in plan
    ]
    _insert_ranks(draw_rows, rank_rows)
    return 'created'


def _extend(ordered, chains, choose, step, record=None):
    remaining = [[name, list(slugs)] for name, slugs in chains]
    while any(slugs for _name, slugs in remaining):
        active = [[name, slugs] for name, slugs in remaining if slugs]
        if len(active) == 1:
            ordered.extend(active[0][1])
            active[0][1].clear()
            break
        total = sum(len(slugs) for _name, slugs in active)
        flat = tuple(sorted(slug for _name, slugs in active for slug in slugs))
        choice = choose(step, flat, total)
        if type(choice) is not int or isinstance(choice, bool) or choice < 0 or choice >= total:
            raise PriorFinishRefused('The scheduling draw did not resolve.')
        snapshot = tuple((name, tuple(slugs)) for name, slugs in active)
        cursor = 0
        chosen = None
        chain_name = None
        for name, slugs in active:
            cursor += len(slugs)
            if choice < cursor:
                chosen = slugs.pop(0)
                chain_name = name
                ordered.append(chosen)
                break
        if chosen is None:
            raise PriorFinishRefused('The scheduling draw did not resolve.')
        if record is not None:
            record(
                step,
                flat,
                _candidates_text(snapshot),
                chosen,
                chain_name,
                tuple(ordered),
            )
        step += 1
    return step


def _candidates_text(active):
    return '|'.join(f'{name}:{",".join(slugs)}' for name, slugs in active)


def _chains(clubs, played):
    buckets = {}
    for club in clubs:
        if played:
            key = (club.conference, club.division)
        else:
            key = ('unplayed', club.slug)
        buckets.setdefault(key, []).append(club)
    chains = []
    for key in sorted(buckets):
        members = buckets[key]
        if played:
            places = [club.place for club in members]
            if len(set(places)) != len(places):
                raise PriorFinishRefused('A 2001 division repeats a place.')
            members.sort(key=lambda club: club.place)
            name = f'{key[0]}:{key[1]}'
        else:
            name = f'unplayed:{key[1]}'
        chains.append((name, tuple(club.slug for club in members)))
    return tuple(chains)


def _percentage_groups(clubs):
    ordered = sorted(clubs, key=cmp_to_key(_better_first))
    groups = []
    for club in ordered:
        if not groups or not _same_percentage(groups[-1][0], club):
            groups.append([club])
        else:
            groups[-1].append(club)
    return groups


def _better_first(left, right):
    if _better(left, right):
        return -1
    if _better(right, left):
        return 1
    return 0


def _better(left, right):
    left_points, left_games = _percentage(left)
    right_points, right_games = _percentage(right)
    return left_points * right_games > right_points * left_games


def _same_percentage(left, right):
    left_points, left_games = _percentage(left)
    right_points, right_games = _percentage(right)
    return left_points * right_games == right_points * left_games


def _percentage(club):
    games = club.wins + club.losses + club.ties
    return 2 * club.wins + club.ties, 2 * games


def _split_clubs(clubs):
    if not clubs:
        raise PriorFinishRefused('A scheduling rank needs a club.')
    slugs = []
    played = []
    unplayed = []
    for club in clubs:
        if not isinstance(club, PriorClub) or not isinstance(club.slug, str) or not club.slug:
            raise PriorFinishRefused('A scheduling rank needs a club.')
        if club.slug in slugs:
            raise PriorFinishRefused('A scheduling rank repeats a club.')
        slugs.append(club.slug)
        if club.conference is None:
            if (
                club.wins is not None
                or club.losses is not None
                or club.ties is not None
                or club.division is not None
                or club.place is not None
            ):
                raise PriorFinishRefused(
                    'A club with no 2001 season has no record and no place.'
                )
            unplayed.append(club)
            continue
        _require_count(club.wins)
        _require_count(club.losses)
        _require_count(club.ties)
        if club.wins + club.losses + club.ties < 1:
            raise PriorFinishRefused('A played 2001 club has no games.')
        if (
            type(club.place) is not int
            or isinstance(club.place, bool)
            or club.place < 1
            or not club.division
        ):
            raise PriorFinishRefused('A played club has no 2001 division place.')
        played.append(club)
    return played, unplayed


def _uniform_index(seed, label, modulus, draw=hmac_int):
    _message, _digest, choice = _resolving_draw(seed, label, modulus, draw=draw)
    return choice


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
    raise PriorFinishRefused('The scheduling draw did not resolve.')


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


def _finish_plan(release_id, finishes, schedule_length):
    if not isinstance(finishes, (list, tuple)) or not finishes:
        raise PriorFinishRefused(
            'The 2001 finishes do not match the stored 2001 membership.'
        )
    memberships = _memberships(release_id, PRIOR_YEAR)
    if len(finishes) != len(memberships):
        raise PriorFinishRefused(
            'The 2001 finishes do not match the stored 2001 membership.'
        )
    plan = []
    seen = set()
    for finish in finishes:
        if not isinstance(finish, Finish):
            raise PriorFinishRefused('A finish needs a whole-number record and place.')
        franchise_id = finish.franchise_id
        if type(franchise_id) is not int or isinstance(franchise_id, bool):
            raise PriorFinishRefused('A finish needs a whole-number record and place.')
        if franchise_id in seen or franchise_id not in memberships:
            raise PriorFinishRefused(
                'The 2001 finishes do not match the stored 2001 membership.'
            )
        seen.add(franchise_id)
        wins = _require_count(finish.wins)
        losses = _require_count(finish.losses)
        ties = _require_count(finish.ties)
        place = finish.division_place
        if type(place) is not int or isinstance(place, bool) or place < 1:
            raise PriorFinishRefused('A finish needs a whole-number record and place.')
        if wins + losses + ties != schedule_length:
            raise PriorFinishRefused(
                'The record uses the stored 2001 schedule length.'
            )
        membership = memberships[franchise_id]
        plan.append({
            'franchise_id': franchise_id,
            'wins': wins,
            'losses': losses,
            'ties': ties,
            'division_place': place,
            'conference_slug': membership.conference_slug,
            'division_slug': membership.division_slug,
        })
    if seen != set(memberships):
        raise PriorFinishRefused(
            'The 2001 finishes do not match the stored 2001 membership.'
        )
    _require_division_resolution(plan)
    return plan


def _require_consistent(release_id, rows, schedule_length):
    memberships = _memberships(release_id, PRIOR_YEAR)
    if len(rows) != len(memberships):
        raise PriorFinishRefused(
            'The 2001 finishes do not match the stored 2001 membership.'
        )
    plan = []
    seen = set()
    for row in rows:
        if row.franchise_id in seen or row.franchise_id not in memberships:
            raise PriorFinishRefused(
                'The 2001 finishes do not match the stored 2001 membership.'
            )
        seen.add(row.franchise_id)
        if row.wins + row.losses + row.ties != schedule_length:
            raise PriorFinishRefused(
                'The record uses the stored 2001 schedule length.'
            )
        membership = memberships[row.franchise_id]
        if (
            row.conference_slug != membership.conference_slug
            or row.division_slug != membership.division_slug
        ):
            raise PriorFinishRefused(
                'The 2001 place uses the franchise\'s stored 2001 division.'
            )
        plan.append({
            'franchise_id': row.franchise_id,
            'wins': row.wins,
            'losses': row.losses,
            'ties': row.ties,
            'division_place': row.division_place,
            'conference_slug': row.conference_slug,
            'division_slug': row.division_slug,
        })
    _require_division_resolution(plan)


def _require_division_resolution(plan):
    divisions = {}
    for item in plan:
        divisions.setdefault(
            (item['conference_slug'], item['division_slug']),
            [],
        ).append(item)
    for members in divisions.values():
        places = [item['division_place'] for item in members]
        if sorted(places) != list(range(1, len(members) + 1)):
            raise PriorFinishRefused(
                'A 2001 division place is outside that division.'
            )
        ordered = sorted(members, key=lambda item: item['division_place'])
        for earlier, later in zip(ordered, ordered[1:]):
            if _record_better(later, earlier):
                raise PriorFinishRefused(
                    'A better 2001 winning percentage has the worse place.'
                )


def _record_better(left, right):
    left_club = PriorClub(
        '', left['wins'], left['losses'], left['ties'], 'c', 'd', 1,
    )
    right_club = PriorClub(
        '', right['wins'], right['losses'], right['ties'], 'c', 'd', 1,
    )
    return _better(left_club, right_club)


def _memberships(release_id, season_year):
    found = {}
    rows = FranchiseSeasonMembership.objects.filter(
        franchise__release_id=release_id,
        season_year=season_year,
    ).select_related('franchise')
    for row in rows:
        if row.franchise_id in found:
            raise PriorFinishRefused('The 2001 membership repeats a franchise.')
        found[row.franchise_id] = row
    return found


def _seats(release_id, season_year):
    return [
        {
            'franchise_id': row.franchise_id,
            'slug': row.franchise.placeholder_slug,
            'conference': row.conference_slug,
            'division': row.division_slug,
        }
        for row in FranchiseSeasonMembership.objects.filter(
            franchise__release_id=release_id,
            season_year=season_year,
        ).select_related('franchise')
    ]


def _groups(seats):
    groups = {}
    seen = set()
    for seat in seats:
        if seat['franchise_id'] in seen:
            raise PriorFinishRefused(
                'The 2002 alignment is not eight divisions of four.'
            )
        seen.add(seat['franchise_id'])
        if (
            seat['conference'] not in ('conference-1', 'conference-2')
            or seat['division'] not in ('d1', 'd2', 'd3', 'd4')
        ):
            raise PriorFinishRefused(
                'The 2002 alignment is not eight divisions of four.'
            )
        groups.setdefault((seat['conference'], seat['division']), []).append(seat)
    if len(seats) != 32 or len(groups) != 8:
        raise PriorFinishRefused('The 2002 alignment is not eight divisions of four.')
    for group in groups.values():
        if len(group) != 4 or len({seat['slug'] for seat in group}) != 4:
            raise PriorFinishRefused(
                'The 2002 alignment is not eight divisions of four.'
            )
    return groups


def _finish_key(row):
    return (
        row.franchise_id,
        row.wins,
        row.losses,
        row.ties,
        row.division_place,
        row.conference_slug,
        row.division_slug,
    )


def _finish_plan_key(item):
    return (
        item['franchise_id'],
        item['wins'],
        item['losses'],
        item['ties'],
        item['division_place'],
        item['conference_slug'],
        item['division_slug'],
    )


def _rank_key(row):
    return (
        row.franchise_id,
        row.scheduling_rank,
        row.conference_slug,
        row.division_slug,
    )


def _rank_plan_key(item):
    return (
        item['franchise_id'],
        item['scheduling_rank'],
        item['conference_slug'],
        item['division_slug'],
    )


def _require_count(value):
    if (
        type(value) is not int
        or isinstance(value, bool)
        or value < 0
        or value > 32767
    ):
        raise PriorFinishRefused('A finish needs a whole-number record and place.')
    return value


def _require_id(timeline_id):
    if isinstance(timeline_id, bool) or not isinstance(timeline_id, int):
        raise PriorFinishRefused('The timeline is missing.')


def _require_published(release):
    if release.status != release.Status.PUBLISHED:
        raise PriorFinishRefused('The release is not published.')


def _lock_timeline(timeline_id, release_id):
    try:
        timeline = Timeline.objects.select_for_update().get(pk=timeline_id)
    except Timeline.DoesNotExist as exc:
        raise PriorFinishRefused('The timeline is missing.') from exc
    if timeline.release_id != release_id:
        raise PriorFinishRefused('The timeline is missing.')
    if timeline.status != Timeline.Status.ACTIVE:
        raise PriorFinishRefused('The timeline is not active.')
    return timeline


def _lock_prior_season(timeline):
    season = (
        TimelineSeason.objects.select_for_update()
        .filter(timeline_id=timeline.pk, season_year=PRIOR_YEAR)
        .select_related('profile')
        .first()
    )
    if season is None or not season.regular_season_simulated:
        raise PriorFinishRefused('The 2001 regular season is not complete.')
    return season


def _clean_rows(rows, message):
    try:
        for row in rows:
            row.full_clean()
    except ValidationError as exc:
        raise PriorFinishRefused(message) from exc


def _insert_finishes(rows):
    try:
        with transaction.atomic():
            with writing_transition_rows():
                _clean_rows(rows, 'A 2001 finish failed validation.')
                TimelinePriorFinish.objects.bulk_create(rows)
    except IntegrityError as exc:
        raise PriorFinishRefused('The 2001 finish insert failed.') from exc


def _insert_ranks(draw_rows, rank_rows):
    try:
        with transaction.atomic():
            with writing_transition_rows():
                _clean_rows(draw_rows, 'A scheduling draw failed validation.')
                _clean_rows(rank_rows, 'A scheduling rank failed validation.')
                if draw_rows:
                    TimelineSchedulingDraw.objects.bulk_create(draw_rows)
                TimelineSchedulingRank.objects.bulk_create(rank_rows)
    except IntegrityError as exc:
        raise PriorFinishRefused('The scheduling rank insert failed.') from exc
