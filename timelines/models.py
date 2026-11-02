"""Timeline campaigns, human participants, and the simulation-audit foundation.

Timeline rows point at shared universe identities. They do not copy the
release. Published-release data is not written here.

Player identity, the initial 33-component profile, and potential stay on
the universe player. A campaign stores an overlay, one rating row per
season, roster membership, and development events. This module does not
copy those shared facts, and it does not create campaigns or play seasons.

Deleting a timeline cascades to its own rows. Shared releases, franchises,
people, rules, and expansion events use PROTECT, so a timeline delete cannot
remove them. The account policy in the model proposal deleted a campaign
when its single owner was deleted. A timeline can later have more than one
human, so the creator and each participant use a nullable user reference
with SET_NULL. Removing an account leaves the campaign and its control
history in place. It does not delete another participant's timeline. An
active participant whose user is then null is no longer a playable human,
and that franchise is treated as bot-controlled until a later recovery
service reassigns it. Phase 4 does not implement that service.

Random draws are not stored. A later service recomputes them from the
universe seed and the stable coordinates. The timeline id, the participant,
and owner decisions are not inputs to that draw. This module does not
simulate, and it does not insert campaigns.
"""

import contextvars
from contextlib import contextmanager

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction

from universe.people import (
    GM_PRIORITIES,
    PLAYER_COMPONENTS,
    POSITIONS,
    PlayerRatingProfile,
    UniversePlayer,
)
from universe.rules import EraRuleProfile, ExpansionClub, ExpansionEvent


OPEN_SEASON = 32767

PHASES = (
    'choose_franchise',
    'staff',
    'direction',
    'draft',
    'instructions',
    'roster_cut',
    'regular',
    'playoffs',
    'offseason',
    'complete',
)

TIMELINE_STATUSES = ('active', 'completed', 'archived')

PARTICIPANT_STATUSES = ('active', 'departed')

CONTROL_REASONS = (
    'expansion_switch',
    'departed',
    'timeline_complete',
    'released',
)

EXPANSION_GATES = ('not_expansion', 'ready', 'blocked')

TEAM_DIRECTIONS = ('compete', 'balanced', 'rebuild')

TRADE_APPETITES = ('low', 'medium', 'high')

VETERAN_PREFERENCES = ('automatic', 'youth', 'balanced', 'veterans')

DRAFT_PARTICIPATION = ('owner_selects', 'delegate_to_gm')

OFFENSIVE_EMPHASIS = ('run', 'balanced', 'pass')

DEFENSIVE_EMPHASIS = ('stop_run', 'balanced', 'pressure_quarterback')

FOURTH_DOWN = ('conservative', 'balanced', 'aggressive')

PLAYING_TIME = ('automatic', 'veterans', 'balanced', 'develop_youth')

EXPANSION_DECISIONS = ('stay', 'apply')

EXPANSION_RESOLUTIONS = ('pending', 'awarded', 'not_awarded', 'withdrawn')

SELECTION_METHODS = ('not_needed', 'automatic', 'fair_draw')

PLAYER_STATUSES = (
    'prospect',
    'active',
    'taxi',
    'reserve',
    'free_agent',
    'retired',
)

ROSTER_ROLES = ('starter', 'backup', 'taxi', 'reserve', 'free_agent')

ROSTER_AVAILABILITY = ('available', 'unavailable')

ROSTER_CLOSE_REASONS = (
    'cut',
    'transaction',
    'expansion',
    'retired',
    'season_complete',
)

DEVELOPMENT_REASONS = ('age', 'snaps', 'injury', 'coaching')

RESERVE_BLOCKED_PHASES = (
    'choose_franchise',
    'staff',
    'direction',
    'draft',
    'instructions',
)

_deferred_players = contextvars.ContextVar('tl_deferred_players', default=None)


def _choices(values):
    return [(value, value) for value in values]


def _previous(instance):
    if not instance.pk:
        return None
    return instance.__class__.objects.filter(pk=instance.pk).first()


def _stored_names(instance):
    names = []
    for field in instance._meta.concrete_fields:
        if getattr(field, 'generated', False) or field.attname == 'id':
            continue
        names.append(field.attname)
    return names


def _changed(previous, instance, names):
    return [
        name for name in names
        if getattr(previous, name) != getattr(instance, name)
    ]


def _range_end(value):
    return OPEN_SEASON if value is None else value


def _overlaps(first_a, final_a, first_b, final_b):
    return first_a <= _range_end(final_b) and first_b <= _range_end(final_a)


def _status_for_role(role):
    if role in ('starter', 'backup'):
        return 'active'
    return role


def _occupied_weeks(start_week, end_week, exclude_end):
    if end_week is None:
        return (start_week, None)
    last = end_week - 1 if exclude_end else end_week
    if last < start_week:
        return None
    return (start_week, last)


def _weeks_conflict(start_a, end_a, start_b, end_b):
    """Inclusive week spans in one season.

    A later row may start on the earlier row's end week. That boundary week
    belongs to the later row, so the earlier row does not occupy it.
    """
    span_a = _occupied_weeks(
        start_a,
        end_a,
        end_a is not None and start_b == end_a,
    )
    span_b = _occupied_weeks(
        start_b,
        end_b,
        end_b is not None and start_a == end_b,
    )
    if span_a is None or span_b is None:
        return False
    first_a, last_a = span_a
    first_b, last_b = span_b
    if last_a is None and last_b is None:
        return True
    if last_a is None:
        return last_b >= first_a
    if last_b is None:
        return last_a >= first_b
    return first_a <= last_b and first_b <= last_a


def _check_lifecycle(player, rows, errors, field='status'):
    open_rows = [row for row in rows if row.end_week is None]
    if player.status == player.Status.PROSPECT:
        if rows:
            errors[field] = 'A prospect has no roster membership.'
        return
    if player.status == player.Status.RETIRED:
        if open_rows:
            errors[field] = 'A retired player has no open roster membership.'
            return
        if not rows:
            errors[field] = 'Retirement closes a roster membership.'
            return
        matched = False
        for row in rows:
            if row.reason_ended != 'retired' or not row.season_id:
                continue
            if row.season.season_year == player.retired_year:
                matched = True
                break
        if not matched:
            errors[field] = (
                'The retirement season has a membership closed as retired.'
            )
        return
    if len(open_rows) != 1:
        errors[field] = 'A player in the league has one open roster membership.'
        return
    expected = _status_for_role(open_rows[0].role)
    if player.status != expected:
        errors[field] = 'Lifecycle status matches the open roster membership.'


def _deferring():
    return _deferred_players.get() is not None


def _note_deferred(player_id):
    pending = _deferred_players.get()
    if pending is not None and player_id is not None:
        pending.add(player_id)


def _validate_deferred(player_ids):
    if not player_ids:
        return
    problems = {}
    players = TimelinePlayer.objects.filter(pk__in=player_ids).prefetch_related(
        'roster_memberships__season',
    )
    for player in players:
        row_errors = {}
        _check_lifecycle(
            player,
            list(player.roster_memberships.all()),
            row_errors,
        )
        if row_errors:
            problems[str(player.pk)] = row_errors
    if problems:
        raise ValidationError(problems)


@contextmanager
def deferred_lifecycle():
    """Save a player and roster change, then check the pair.

    ``full_clean`` on either row rejects a state that does not match the
    lifecycle table. A cut, signing, position change, role change, or
    retirement writes the membership and the status in one transaction, so
    the intermediate row would not match. Inside this block those cross-row
    checks wait until both saves have run. The block rolls back when the
    final state does not match. Other field checks still run on each save.
    """
    if _deferring():
        yield
        return
    with transaction.atomic():
        token = _deferred_players.set(set())
        try:
            yield
            _validate_deferred(_deferred_players.get())
        finally:
            _deferred_players.reset(token)


class Timeline(models.Model):
    """One campaign. Humans join through TimelineParticipant, not a single owner column."""

    class Phase(models.TextChoices):
        CHOOSE_FRANCHISE = 'choose_franchise', 'choose_franchise'
        STAFF = 'staff', 'staff'
        DIRECTION = 'direction', 'direction'
        DRAFT = 'draft', 'draft'
        INSTRUCTIONS = 'instructions', 'instructions'
        ROSTER_CUT = 'roster_cut', 'roster_cut'
        REGULAR = 'regular', 'regular'
        PLAYOFFS = 'playoffs', 'playoffs'
        OFFSEASON = 'offseason', 'offseason'
        COMPLETE = 'complete', 'complete'

    class Status(models.TextChoices):
        ACTIVE = 'active', 'active'
        COMPLETED = 'completed', 'completed'
        ARCHIVED = 'archived', 'archived'

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='timelines_created',
    )
    release = models.ForeignKey(
        'universe.UniverseRelease',
        on_delete=models.PROTECT,
        related_name='timelines',
    )
    name = models.CharField(max_length=255)
    start_season_year = models.PositiveSmallIntegerField()
    current_season_year = models.PositiveSmallIntegerField()
    season_index = models.PositiveSmallIntegerField()
    phase = models.CharField(max_length=32, choices=Phase.choices)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    decision_audit = models.CharField(max_length=64, null=True, blank=True)
    persist_snap_draws = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at', 'id']
        indexes = [
            models.Index(fields=['release', 'status'], name='tl_release_status_idx'),
            models.Index(fields=['created_by', 'release'], name='tl_creator_release_idx'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(name=''),
                name='tl_timeline_name',
            ),
            models.CheckConstraint(
                condition=models.Q(phase__in=PHASES),
                name='tl_timeline_phase',
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=TIMELINE_STATUSES),
                name='tl_timeline_status',
            ),
            models.CheckConstraint(
                condition=models.Q(season_index__gte=1),
                name='tl_timeline_index',
            ),
            models.CheckConstraint(
                condition=models.Q(
                    current_season_year__gte=models.F('start_season_year'),
                ),
                name='tl_timeline_years',
            ),
            models.CheckConstraint(
                condition=models.Q(
                    current_season_year=(
                        models.F('start_season_year') + models.F('season_index') - 1
                    ),
                ),
                name='tl_timeline_year_index',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status='active', completed_at__isnull=True)
                    | (
                        ~models.Q(status='active')
                        & models.Q(completed_at__isnull=False)
                    )
                ),
                name='tl_timeline_completed_at',
            ),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        errors = {}
        release = self.release if self.release_id else None
        if release is not None:
            if self.start_season_year < release.first_season_year or (
                self.start_season_year > release.last_season_year
            ):
                errors['start_season_year'] = (
                    'The start season is inside the release.'
                )
            if self.current_season_year < release.first_season_year or (
                self.current_season_year > release.last_season_year
            ):
                errors['current_season_year'] = (
                    'The current season is inside the release.'
                )
            if release.status != release.Status.PUBLISHED and not self.pk:
                errors['release'] = 'A timeline uses a published release.'
        if self.status == self.Status.COMPLETED and self.phase != self.Phase.COMPLETE:
            errors['phase'] = 'A completed timeline is in the complete phase.'
        previous = _previous(self)
        if previous is not None:
            if previous.release_id != self.release_id:
                errors['release'] = 'A timeline cannot change its release.'
            if previous.start_season_year != self.start_season_year:
                errors['start_season_year'] = 'The start season is fixed.'
            if previous.created_by_id != self.created_by_id:
                errors['created_by'] = 'The creating account is fixed.'
            if previous.status == self.Status.ARCHIVED:
                errors['status'] = 'An archived timeline cannot be changed.'
            elif previous.status == self.Status.COMPLETED:
                changed = _changed(previous, self, _stored_names(self))
                allowed = {'status', 'updated_at'}
                if self.status != self.Status.ARCHIVED or (set(changed) - allowed):
                    errors['status'] = (
                        'A completed timeline can only be archived.'
                    )
            elif previous.status == self.Status.ACTIVE and self.status != self.Status.ACTIVE:
                if self.completed_at is None:
                    errors['completed_at'] = (
                        'Closing a timeline records when it closed.'
                    )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class TimelineChild(models.Model):
    class Meta:
        abstract = True

    def bound_timeline(self):
        raise NotImplementedError

    def extra_clean(self, errors):
        return None

    def clean(self):
        super().clean()
        errors = {}
        timeline = None
        try:
            timeline = self.bound_timeline()
        except (self.DoesNotExist, AttributeError):
            timeline = None
        if timeline is not None and timeline.status != Timeline.Status.ACTIVE:
            if not self.pk:
                errors['timeline'] = (
                    'A completed or archived timeline cannot gain rows.'
                )
            else:
                previous = _previous(self)
                if previous is not None:
                    for name in _changed(previous, self, _stored_names(self)):
                        if name == 'updated_at':
                            continue
                        errors[name] = (
                            'A completed or archived timeline cannot change this row.'
                        )
        if not errors:
            self.extra_clean(errors)
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        timeline = self.bound_timeline()
        if timeline is not None and timeline.status != Timeline.Status.ACTIVE:
            raise ValidationError(
                'A completed or archived timeline cannot lose rows except by deleting the timeline.'
            )
        return super().delete(*args, **kwargs)


class TimelineParticipant(TimelineChild):
    """One human in a campaign. A single-player timeline has one active row.

    Bot-controlled franchises do not get a participant. A future multiplayer
    timeline adds more rows. This model does not invite, wait, or synchronize.

    Deleting the account sets ``user`` null and leaves the row. That person
    is no longer a playable human. Historical control rows stay. The franchise
    is treated as bot-controlled until a later account-recovery or
    commissioner service reassigns it. This phase does not reassign it.
    """

    class Status(models.TextChoices):
        ACTIVE = 'active', 'active'
        DEPARTED = 'departed', 'departed'

    timeline = models.ForeignKey(
        Timeline,
        on_delete=models.CASCADE,
        related_name='participants',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='timeline_participations',
    )
    status = models.CharField(max_length=16, choices=Status.choices)
    joined_season = models.PositiveSmallIntegerField()
    departed_at = models.DateTimeField(null=True, blank=True)
    joined_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['timeline', 'joined_at', 'id']
        indexes = [
            models.Index(fields=['timeline', 'status'], name='tl_participant_status_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['timeline', 'user'],
                name='unique_tl_participant',
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=PARTICIPANT_STATUSES),
                name='tl_participant_status',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status='active', departed_at__isnull=True)
                    | (
                        models.Q(status='departed')
                        & models.Q(departed_at__isnull=False)
                    )
                ),
                name='tl_participant_departure',
            ),
        ]

    def __str__(self):
        return f'{self.timeline_id} participant {self.user_id}'

    def bound_timeline(self):
        if not self.timeline_id:
            return None
        return self.timeline

    def extra_clean(self, errors):
        if not self.timeline_id:
            return
        timeline = self.timeline
        release = timeline.release
        if self.status == self.Status.ACTIVE and not self.user_id:
            errors['user'] = 'An active participant has an account.'
        if self.joined_season < timeline.start_season_year or (
            self.joined_season > release.last_season_year
        ):
            errors['joined_season'] = (
                'The joined season is inside this timeline and its release.'
            )
        previous = _previous(self)
        if previous is not None:
            if previous.timeline_id != self.timeline_id:
                errors['timeline'] = 'A participant cannot change timelines.'
            if previous.user_id != self.user_id:
                errors['user'] = 'A participant cannot change accounts.'
            if previous.joined_season != self.joined_season:
                errors['joined_season'] = 'The joined season is fixed.'
            if (
                previous.status == self.Status.DEPARTED
                and _changed(previous, self, _stored_names(self))
            ):
                errors['status'] = 'A departed participant cannot be changed.'


class TimelineFranchiseControl(TimelineChild):
    """History of which human controls which franchise.

    An open row has a null final season. A franchise with no open row is
    bot-controlled. Overlapping stints are rejected in application validation.
    MySQL unique indexes do not collapse those ranges, so the open row is also
    stored in generated keys.

    If the participant's account has been deleted, an open stint remains as
    history, but it is not a playable human control. The franchise is
    bot-controlled until a later service reassigns it.
    """

    timeline = models.ForeignKey(
        Timeline,
        on_delete=models.CASCADE,
        related_name='franchise_controls',
    )
    participant = models.ForeignKey(
        TimelineParticipant,
        on_delete=models.CASCADE,
        related_name='franchise_controls',
    )
    franchise = models.ForeignKey(
        'universe.UniverseFranchise',
        on_delete=models.PROTECT,
        related_name='timeline_controls',
    )
    first_controlled_season = models.PositiveSmallIntegerField()
    final_controlled_season = models.PositiveSmallIntegerField(null=True, blank=True)
    reason_ended = models.CharField(
        max_length=32,
        choices=_choices(CONTROL_REASONS),
        null=True,
        blank=True,
    )
    open_participant_key = models.GeneratedField(
        expression=models.Case(
            models.When(
                final_controlled_season__isnull=True,
                then=models.F('participant_id'),
            ),
            default=models.Value(None),
            output_field=models.BigIntegerField(null=True),
        ),
        output_field=models.BigIntegerField(null=True),
        db_persist=True,
    )
    open_franchise_key = models.GeneratedField(
        expression=models.Case(
            models.When(
                final_controlled_season__isnull=True,
                then=models.F('franchise_id'),
            ),
            default=models.Value(None),
            output_field=models.BigIntegerField(null=True),
        ),
        output_field=models.BigIntegerField(null=True),
        db_persist=True,
    )

    class Meta:
        ordering = ['timeline', 'franchise', 'first_controlled_season']
        indexes = [
            models.Index(
                fields=['timeline', 'franchise'],
                name='tl_control_franchise_idx',
            ),
            models.Index(
                fields=['participant', 'first_controlled_season'],
                name='tl_control_participant_idx',
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['open_participant_key'],
                name='unique_tl_open_participant',
            ),
            models.UniqueConstraint(
                fields=['timeline', 'open_franchise_key'],
                name='unique_tl_open_franchise',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        final_controlled_season__isnull=True,
                        reason_ended__isnull=True,
                    )
                    | models.Q(
                        final_controlled_season__gte=models.F('first_controlled_season'),
                        reason_ended__in=CONTROL_REASONS,
                    )
                ),
                name='tl_control_open_or_closed',
            ),
        ]

    def __str__(self):
        return f'{self.timeline_id} {self.franchise_id}'

    def bound_timeline(self):
        if not self.timeline_id:
            return None
        return self.timeline

    def extra_clean(self, errors):
        if not self.timeline_id or not self.participant_id or not self.franchise_id:
            return
        timeline = self.timeline
        if self.participant.timeline_id != self.timeline_id:
            errors['participant'] = 'The participant belongs to this timeline.'
        release = timeline.release
        franchise = self.franchise
        if franchise.release_id != timeline.release_id:
            errors['franchise'] = 'The franchise belongs to this timeline\'s release.'
        if self.first_controlled_season < timeline.start_season_year or (
            self.first_controlled_season > release.last_season_year
        ):
            errors['first_controlled_season'] = (
                'Control starts inside the timeline and the release.'
            )
        if self.final_controlled_season is not None and (
            self.final_controlled_season > release.last_season_year
        ):
            errors['final_controlled_season'] = (
                'Control ends inside the release.'
            )
        if self.first_controlled_season < franchise.first_active_season:
            errors['first_controlled_season'] = (
                'Control cannot start before the franchise is active.'
            )
        if franchise.final_active_season is not None:
            end = _range_end(self.final_controlled_season)
            if end > franchise.final_active_season or (
                self.first_controlled_season > franchise.final_active_season
            ):
                errors['franchise'] = (
                    'Control stays inside the franchise\'s active seasons.'
                )
        self._reject_overlap(errors)
        previous = _previous(self)
        if previous is not None:
            frozen = (
                'timeline_id',
                'participant_id',
                'franchise_id',
                'first_controlled_season',
            )
            for name in frozen:
                if getattr(previous, name) != getattr(self, name):
                    errors[name] = 'A control stint keeps its franchise and start.'
            if previous.final_controlled_season is not None and _changed(
                previous, self, _stored_names(self)
            ):
                errors['final_controlled_season'] = 'A closed control stint stays closed.'

    def _reject_overlap(self, errors):
        others = TimelineFranchiseControl.objects.filter(timeline_id=self.timeline_id)
        if self.pk:
            others = others.exclude(pk=self.pk)
        for other in others.filter(participant_id=self.participant_id):
            if _overlaps(
                self.first_controlled_season,
                self.final_controlled_season,
                other.first_controlled_season,
                other.final_controlled_season,
            ):
                errors['participant'] = (
                    'A participant controls one franchise at a time.'
                )
                break
        for other in others.filter(franchise_id=self.franchise_id):
            if _overlaps(
                self.first_controlled_season,
                self.final_controlled_season,
                other.first_controlled_season,
                other.final_controlled_season,
            ):
                errors['franchise'] = (
                    'A franchise has one human controller at a time.'
                )
                break


class TimelineSeason(TimelineChild):
    """One playable year of one campaign. Rows are created at season entry, not all 60 here."""

    timeline = models.ForeignKey(
        Timeline,
        on_delete=models.CASCADE,
        related_name='seasons',
    )
    profile = models.ForeignKey(
        EraRuleProfile,
        on_delete=models.PROTECT,
        related_name='timeline_seasons',
    )
    season_year = models.PositiveSmallIntegerField()
    season_index = models.PositiveSmallIntegerField()
    phase = models.CharField(max_length=32, choices=Timeline.Phase.choices)
    expansion_gate = models.CharField(
        max_length=16,
        choices=_choices(EXPANSION_GATES),
        default='not_expansion',
    )
    setup_complete = models.BooleanField(default=False)
    directives_locked = models.BooleanField(default=False)
    regular_season_simulated = models.BooleanField(default=False)
    postseason_complete = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['timeline', 'season_year']
        constraints = [
            models.UniqueConstraint(
                fields=['timeline', 'season_year'],
                name='unique_tl_season',
            ),
            models.CheckConstraint(
                condition=models.Q(phase__in=PHASES),
                name='tl_season_phase',
            ),
            models.CheckConstraint(
                condition=models.Q(expansion_gate__in=EXPANSION_GATES),
                name='tl_season_expansion_gate',
            ),
            models.CheckConstraint(
                condition=models.Q(season_index__gte=1),
                name='tl_season_index',
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(directives_locked=True)
                    | models.Q(setup_complete=True)
                ),
                name='tl_season_directives_ready',
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(regular_season_simulated=True)
                    | models.Q(directives_locked=True)
                ),
                name='tl_season_regular_locked',
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(postseason_complete=True)
                    | models.Q(regular_season_simulated=True)
                ),
                name='tl_season_postseason_order',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(phase='complete', completed_at__isnull=False)
                    | (
                        ~models.Q(phase='complete')
                        & models.Q(completed_at__isnull=True)
                    )
                ),
                name='tl_season_completed_at',
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(phase='complete')
                    | models.Q(postseason_complete=True)
                ),
                name='tl_season_complete_chain',
            ),
        ]
        indexes = [
            models.Index(fields=['profile', 'season_year'], name='tl_season_profile_idx'),
        ]

    def __str__(self):
        return f'{self.timeline_id} {self.season_year}'

    def bound_timeline(self):
        if not self.timeline_id:
            return None
        return self.timeline

    def extra_clean(self, errors):
        if not self.timeline_id or not self.profile_id:
            return
        timeline = self.timeline
        release = timeline.release
        if self.season_year < timeline.start_season_year or (
            self.season_year > release.last_season_year
        ):
            errors['season_year'] = 'The season is inside this timeline and its release.'
        expected = timeline.start_season_year + self.season_index - 1
        if self.season_year != expected:
            errors['season_index'] = (
                'The season index counts from this timeline\'s start season.'
            )
        profile = self.profile
        if profile.release_id != timeline.release_id:
            errors['profile'] = 'The era profile belongs to this timeline\'s release.'
        elif self.season_year < profile.first_year or self.season_year > profile.last_year:
            errors['profile'] = 'The era profile covers this season.'
        previous = _previous(self)
        if previous is not None:
            for name in ('timeline_id', 'season_year', 'season_index', 'profile_id'):
                if getattr(previous, name) != getattr(self, name):
                    errors[name] = 'The season and its era profile are fixed.'
        if self.pk and self.directives_locked:
            if OwnerDirective.objects.filter(season_id=self.pk, locked=False).exists():
                errors['directives_locked'] = (
                    'An unlocked owner directive cannot remain after the season locks directives.'
                )


class OwnerDirective(TimelineChild):
    """Broad instructions for one human participant and one season.

    The GM still handles contracts, cuts, trades, and roster balance. The
    head coach still handles depth charts, lineups, and play calls. Nothing
    here sets a player component.
    """

    timeline = models.ForeignKey(
        Timeline,
        on_delete=models.CASCADE,
        related_name='directives',
    )
    participant = models.ForeignKey(
        TimelineParticipant,
        on_delete=models.CASCADE,
        related_name='directives',
    )
    season = models.ForeignKey(
        TimelineSeason,
        on_delete=models.CASCADE,
        related_name='directives',
    )
    team_direction = models.CharField(
        max_length=16,
        choices=_choices(TEAM_DIRECTIONS),
        default='balanced',
    )
    priority_group_1 = models.CharField(
        max_length=32,
        choices=_choices(GM_PRIORITIES),
        null=True,
        blank=True,
    )
    priority_group_2 = models.CharField(
        max_length=32,
        choices=_choices(GM_PRIORITIES),
        null=True,
        blank=True,
    )
    priority_group_3 = models.CharField(
        max_length=32,
        choices=_choices(GM_PRIORITIES),
        null=True,
        blank=True,
    )
    trade_appetite = models.CharField(
        max_length=16,
        choices=_choices(TRADE_APPETITES),
        default='medium',
    )
    veteran_preference = models.CharField(
        max_length=16,
        choices=_choices(VETERAN_PREFERENCES),
        default='automatic',
    )
    draft_participation = models.CharField(
        max_length=32,
        choices=_choices(DRAFT_PARTICIPATION),
        default='delegate_to_gm',
    )
    offensive_emphasis = models.CharField(
        max_length=16,
        choices=_choices(OFFENSIVE_EMPHASIS),
        default='balanced',
    )
    defensive_emphasis = models.CharField(
        max_length=32,
        choices=_choices(DEFENSIVE_EMPHASIS),
        default='balanced',
    )
    fourth_down = models.CharField(
        max_length=16,
        choices=_choices(FOURTH_DOWN),
        default='balanced',
    )
    playing_time = models.CharField(
        max_length=16,
        choices=_choices(PLAYING_TIME),
        default='automatic',
    )
    locked = models.BooleanField(default=False)

    class Meta:
        ordering = ['season', 'participant']
        constraints = [
            models.UniqueConstraint(
                fields=['participant', 'season'],
                name='unique_tl_directive',
            ),
            models.CheckConstraint(
                condition=models.Q(team_direction__in=TEAM_DIRECTIONS),
                name='tl_directive_direction',
            ),
            models.CheckConstraint(
                condition=models.Q(trade_appetite__in=TRADE_APPETITES),
                name='tl_directive_trade',
            ),
            models.CheckConstraint(
                condition=models.Q(veteran_preference__in=VETERAN_PREFERENCES),
                name='tl_directive_veterans',
            ),
            models.CheckConstraint(
                condition=models.Q(draft_participation__in=DRAFT_PARTICIPATION),
                name='tl_directive_draft',
            ),
            models.CheckConstraint(
                condition=models.Q(offensive_emphasis__in=OFFENSIVE_EMPHASIS),
                name='tl_directive_offense',
            ),
            models.CheckConstraint(
                condition=models.Q(defensive_emphasis__in=DEFENSIVE_EMPHASIS),
                name='tl_directive_defense',
            ),
            models.CheckConstraint(
                condition=models.Q(fourth_down__in=FOURTH_DOWN),
                name='tl_directive_fourth',
            ),
            models.CheckConstraint(
                condition=models.Q(playing_time__in=PLAYING_TIME),
                name='tl_directive_playing_time',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(priority_group_1__isnull=True)
                    | models.Q(priority_group_1__in=GM_PRIORITIES)
                ),
                name='tl_directive_group_1',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(priority_group_2__isnull=True)
                    | models.Q(priority_group_2__in=GM_PRIORITIES)
                ),
                name='tl_directive_group_2',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(priority_group_3__isnull=True)
                    | models.Q(priority_group_3__in=GM_PRIORITIES)
                ),
                name='tl_directive_group_3',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(priority_group_2__isnull=True)
                    | models.Q(priority_group_1__isnull=False)
                ),
                name='tl_directive_group_order_2',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(priority_group_3__isnull=True)
                    | models.Q(priority_group_2__isnull=False)
                ),
                name='tl_directive_group_order_3',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(priority_group_2__isnull=True)
                    | ~models.Q(priority_group_2=models.F('priority_group_1'))
                ),
                name='tl_directive_group_distinct_2',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(priority_group_3__isnull=True)
                    | (
                        ~models.Q(priority_group_3=models.F('priority_group_1'))
                        & ~models.Q(priority_group_3=models.F('priority_group_2'))
                    )
                ),
                name='tl_directive_group_distinct_3',
            ),
        ]
        indexes = [
            models.Index(fields=['timeline', 'season'], name='tl_directive_season_idx'),
        ]

    def __str__(self):
        return f'{self.participant_id} {self.season_id}'

    def bound_timeline(self):
        if not self.timeline_id:
            return None
        return self.timeline

    def extra_clean(self, errors):
        if not self.timeline_id or not self.participant_id or not self.season_id:
            return
        if self.participant.timeline_id != self.timeline_id:
            errors['participant'] = 'The participant belongs to this timeline.'
        if self.season.timeline_id != self.timeline_id:
            errors['season'] = 'The season belongs to this timeline.'
        previous = _previous(self)
        if previous is not None and previous.locked:
            if _changed(previous, self, _stored_names(self)):
                errors['locked'] = (
                    'A directive stays as it was when the season simulation began.'
                )
        elif self.season.directives_locked:
            if previous is None:
                errors['locked'] = 'A locked season cannot gain an owner directive.'
            else:
                changed = set(_changed(previous, self, _stored_names(self)))
                if changed - {'locked'}:
                    errors['locked'] = 'A locked season cannot change the directive.'
                if not self.locked:
                    errors['locked'] = 'A locked season locks its owner directive.'
        elif previous is not None:
            for name in ('timeline_id', 'participant_id', 'season_id'):
                if getattr(previous, name) != getattr(self, name):
                    errors[name] = 'A directive stays with its participant and season.'


class TimelineExpansionChoice(TimelineChild):
    """A human's stay-or-apply decision for one shared expansion event.

    Awarding a club does not itself close the old control stint. A later
    service does that. Several participants may apply for the same club.
    """

    class Decision(models.TextChoices):
        STAY = 'stay', 'stay'
        APPLY = 'apply', 'apply'

    class Resolution(models.TextChoices):
        PENDING = 'pending', 'pending'
        AWARDED = 'awarded', 'awarded'
        NOT_AWARDED = 'not_awarded', 'not_awarded'
        WITHDRAWN = 'withdrawn', 'withdrawn'

    class SelectionMethod(models.TextChoices):
        NOT_NEEDED = 'not_needed', 'not_needed'
        AUTOMATIC = 'automatic', 'automatic'
        FAIR_DRAW = 'fair_draw', 'fair_draw'

    timeline = models.ForeignKey(
        Timeline,
        on_delete=models.CASCADE,
        related_name='expansion_choices',
    )
    participant = models.ForeignKey(
        TimelineParticipant,
        on_delete=models.CASCADE,
        related_name='expansion_choices',
    )
    event = models.ForeignKey(
        ExpansionEvent,
        on_delete=models.PROTECT,
        related_name='timeline_choices',
    )
    controlled_franchise = models.ForeignKey(
        'universe.UniverseFranchise',
        on_delete=models.PROTECT,
        related_name='expansion_choices_from',
    )
    decision = models.CharField(max_length=8, choices=Decision.choices)
    target_franchise = models.ForeignKey(
        'universe.UniverseFranchise',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='expansion_choices_to',
    )
    resolution = models.CharField(
        max_length=16,
        choices=Resolution.choices,
        default=Resolution.PENDING,
    )
    selection_method = models.CharField(
        max_length=16,
        choices=SelectionMethod.choices,
        default=SelectionMethod.NOT_NEEDED,
    )
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['timeline', 'event', 'participant']
        constraints = [
            models.UniqueConstraint(
                fields=['participant', 'event'],
                name='unique_tl_expansion_choice',
            ),
            models.CheckConstraint(
                condition=models.Q(decision__in=EXPANSION_DECISIONS),
                name='tl_choice_decision',
            ),
            models.CheckConstraint(
                condition=models.Q(resolution__in=EXPANSION_RESOLUTIONS),
                name='tl_choice_resolution',
            ),
            models.CheckConstraint(
                condition=models.Q(selection_method__in=SELECTION_METHODS),
                name='tl_choice_method',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(decision='apply', target_franchise__isnull=False)
                    | models.Q(decision='stay', target_franchise__isnull=True)
                ),
                name='tl_choice_target',
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(resolution__in=('awarded', 'not_awarded'))
                    | models.Q(decision='apply')
                ),
                name='tl_choice_award_apply',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(resolution='pending', resolved_at__isnull=True)
                    | (
                        ~models.Q(resolution='pending')
                        & models.Q(resolved_at__isnull=False)
                    )
                ),
                name='tl_choice_resolved_at',
            ),
        ]
        indexes = [
            models.Index(
                fields=['event', 'target_franchise', 'decision'],
                name='tl_choice_target_idx',
            ),
        ]

    def __str__(self):
        return f'{self.participant_id} {self.event_id} {self.decision}'

    def bound_timeline(self):
        if not self.timeline_id:
            return None
        return self.timeline

    def extra_clean(self, errors):
        if not self.timeline_id or not self.participant_id or not self.event_id:
            return
        timeline = self.timeline
        if self.participant.timeline_id != self.timeline_id:
            errors['participant'] = 'The participant belongs to this timeline.'
        if self.event.release_id != timeline.release_id:
            errors['event'] = 'The expansion event belongs to this timeline\'s release.'
        if self.controlled_franchise_id:
            franchise = self.controlled_franchise
            if franchise.release_id != timeline.release_id:
                errors['controlled_franchise'] = (
                    'The controlled franchise belongs to this release.'
                )
        if self.resolution == self.Resolution.PENDING and self.controlled_franchise_id:
            open_control = TimelineFranchiseControl.objects.filter(
                timeline_id=self.timeline_id,
                participant_id=self.participant_id,
                franchise_id=self.controlled_franchise_id,
                final_controlled_season__isnull=True,
            )
            if not open_control.exists():
                errors['controlled_franchise'] = (
                    'The participant currently controls this franchise.'
                )
        if self.target_franchise_id:
            if self.target_franchise.release_id != timeline.release_id:
                errors['target_franchise'] = (
                    'The target franchise belongs to this release.'
                )
            elif not ExpansionClub.objects.filter(
                event_id=self.event_id,
                franchise_id=self.target_franchise_id,
            ).exists():
                errors['target_franchise'] = (
                    'The target is one of this event\'s expansion franchises.'
                )
        if (
            self.decision == self.Decision.STAY
            and self.selection_method != self.SelectionMethod.NOT_NEEDED
        ):
            errors['selection_method'] = 'Staying does not use a selection method.'
        if self.resolution == self.Resolution.AWARDED and self.selection_method not in (
            self.SelectionMethod.AUTOMATIC,
            self.SelectionMethod.FAIR_DRAW,
        ):
            errors['selection_method'] = 'An award records automatic or fair draw.'
        previous = _previous(self)
        if previous is not None:
            for name in ('timeline_id', 'participant_id', 'event_id'):
                if getattr(previous, name) != getattr(self, name):
                    errors[name] = 'The expansion choice keeps its event and participant.'
            if previous.resolution != self.Resolution.PENDING and _changed(
                previous, self, _stored_names(self)
            ):
                errors['resolution'] = 'A resolved expansion choice stays resolved.'


class ExpansionPin(TimelineChild):
    """Up to five shared players an applying owner asks the GM to prioritize.

    The normal mode stores none. These are not expansion selections and not
    a protection list.
    """

    choice = models.ForeignKey(
        TimelineExpansionChoice,
        on_delete=models.CASCADE,
        related_name='pins',
    )
    player = models.ForeignKey(
        UniversePlayer,
        on_delete=models.PROTECT,
        related_name='expansion_pins',
    )
    priority = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ['choice', 'priority']
        constraints = [
            models.UniqueConstraint(
                fields=['choice', 'player'],
                name='unique_tl_pin_player',
            ),
            models.UniqueConstraint(
                fields=['choice', 'priority'],
                name='unique_tl_pin_priority',
            ),
            models.CheckConstraint(
                condition=models.Q(priority__gte=1, priority__lte=5),
                name='tl_pin_priority',
            ),
        ]

    def __str__(self):
        return f'{self.choice_id} {self.priority}'

    def bound_timeline(self):
        if not self.choice_id:
            return None
        return self.choice.timeline

    def extra_clean(self, errors):
        if not self.choice_id or not self.player_id:
            return
        choice = self.choice
        if choice.decision != TimelineExpansionChoice.Decision.APPLY:
            errors['choice'] = 'Pins are allowed only for an expansion application.'
        if self.player.release_id != choice.timeline.release_id:
            errors['player'] = 'The player belongs to this timeline\'s release.'
        previous = _previous(self)
        if previous is not None and _changed(previous, self, _stored_names(self)):
            errors['priority'] = 'A pin is replaced by deleting it, not by editing it.'


class GameKickoffState(TimelineChild):
    """Immutable inputs for one game. ScheduleGame is a later model.

    ``game_key`` must be stable across comparable timelines. It is not derived
    from the timeline id, either primary key, or any other timeline-specific
    identifier. A later schedule service should build it from shared
    coordinates: universe release, season, calendar week or round, home and
    away franchise keys, and game type when needed. The timeline owns this
    row. The key is what lets comparable timelines find the same random
    sequence. This phase does not build that key or the HMAC.

    ``week`` is a calendar week. ``EraRuleProfile.schedule_length`` is games
    played per franchise, not the highest week number. A later schedule model
    decides which weeks exist, byes, and postseason numbering.

    ``inputs`` is an audit snapshot, not the live rating table. The hash
    verifies the payload. It does not replace it, and it is not the random draw.
    """

    timeline = models.ForeignKey(
        Timeline,
        on_delete=models.CASCADE,
        related_name='kickoff_states',
    )
    season = models.ForeignKey(
        TimelineSeason,
        on_delete=models.CASCADE,
        related_name='kickoff_states',
    )
    game_key = models.CharField(max_length=64)
    week = models.PositiveSmallIntegerField()
    home = models.ForeignKey(
        'universe.UniverseFranchise',
        on_delete=models.PROTECT,
        related_name='kickoff_home_games',
    )
    away = models.ForeignKey(
        'universe.UniverseFranchise',
        on_delete=models.PROTECT,
        related_name='kickoff_away_games',
    )
    profile = models.ForeignKey(
        EraRuleProfile,
        on_delete=models.PROTECT,
        related_name='kickoff_states',
    )
    inputs = models.JSONField()
    input_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['timeline', 'season', 'week', 'game_key']
        constraints = [
            models.UniqueConstraint(
                fields=['timeline', 'game_key'],
                name='unique_tl_kickoff_key',
            ),
            models.UniqueConstraint(
                fields=['timeline', 'season', 'week', 'home', 'away'],
                name='unique_tl_kickoff_game',
            ),
            models.CheckConstraint(
                condition=~models.Q(game_key='') & ~models.Q(input_hash=''),
                name='tl_kickoff_identity',
            ),
            models.CheckConstraint(
                condition=models.Q(week__gte=1),
                name='tl_kickoff_week',
            ),
        ]
        indexes = [
            models.Index(fields=['timeline', 'season', 'week'], name='tl_kickoff_week_idx'),
        ]

    def __str__(self):
        return self.game_key

    def bound_timeline(self):
        if not self.timeline_id:
            return None
        return self.timeline

    def extra_clean(self, errors):
        if not self.timeline_id or not self.season_id or not self.profile_id:
            return
        timeline = self.timeline
        season = self.season
        if season.timeline_id != self.timeline_id:
            errors['season'] = 'The season belongs to this timeline.'
        if self.home_id and self.away_id and self.home_id == self.away_id:
            errors['away'] = 'Home and away are different franchises.'
        for field_name in ('home', 'away'):
            if not getattr(self, field_name + '_id'):
                continue
            franchise = getattr(self, field_name)
            if franchise.release_id != timeline.release_id:
                errors[field_name] = 'The franchise belongs to this timeline\'s release.'
            elif franchise.first_active_season > season.season_year or (
                franchise.final_active_season is not None
                and franchise.final_active_season < season.season_year
            ):
                errors[field_name] = 'The franchise is active in this season.'
        profile = self.profile
        if profile.release_id != timeline.release_id:
            errors['profile'] = 'The era profile belongs to this timeline\'s release.'
        elif season.season_year < profile.first_year or season.season_year > profile.last_year:
            errors['profile'] = 'The era profile covers this season.'
        elif self.profile_id != season.profile_id:
            errors['profile'] = 'The kickoff uses the season\'s era profile.'
        if len(self.input_hash or '') != 64:
            errors['input_hash'] = 'The input hash is a 64-character digest.'
        previous = _previous(self)
        if previous is not None and _changed(previous, self, _stored_names(self)):
            errors['game_key'] = 'A kickoff snapshot is written once.'


class SimulationAuditRecord(TimelineChild):
    """One kept event for a kickoff, in event_index order.

    The random draw is absent on purpose. Replay recomputes it from the
    release seed, the season, the week, the franchise keys, the game key,
    the event index, and the event type. Detailed snap payloads stay null
    unless the timeline has opted into them. This model does not generate
    the draw or the outcome.
    """

    kickoff = models.ForeignKey(
        GameKickoffState,
        on_delete=models.CASCADE,
        related_name='audit_records',
    )
    event_index = models.PositiveIntegerField()
    event_type = models.CharField(max_length=32)
    participants = models.JSONField(null=True, blank=True)
    outcome_code = models.CharField(max_length=64)
    pre_state_hash = models.CharField(max_length=64, null=True, blank=True)
    post_state_hash = models.CharField(max_length=64, null=True, blank=True)
    detail = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['kickoff', 'event_index']
        constraints = [
            models.UniqueConstraint(
                fields=['kickoff', 'event_index'],
                name='unique_tl_audit_event',
            ),
            models.CheckConstraint(
                condition=~models.Q(event_type='') & ~models.Q(outcome_code=''),
                name='tl_audit_event',
            ),
        ]
        indexes = [
            models.Index(fields=['kickoff', 'event_type'], name='tl_audit_type_idx'),
        ]

    def __str__(self):
        return f'{self.kickoff_id} {self.event_index}'

    def bound_timeline(self):
        if not self.kickoff_id:
            return None
        return self.kickoff.timeline

    def extra_clean(self, errors):
        if not self.kickoff_id:
            return
        timeline = self.kickoff.timeline
        if self.detail is not None and not timeline.persist_snap_draws:
            errors['detail'] = (
                'Detailed snap payloads stay off unless the timeline stores them.'
            )
        for name in ('pre_state_hash', 'post_state_hash'):
            value = getattr(self, name)
            if value is not None and len(value) != 64:
                errors[name] = 'A state hash is a 64-character digest.'
        previous = _previous(self)
        if previous is not None and _changed(previous, self, _stored_names(self)):
            errors['event_index'] = 'An audit record is written once.'


def _current_rating_constraints():
    constraints = []
    for component in PLAYER_COMPONENTS:
        constraints.append(
            models.CheckConstraint(
                condition=models.Q(**{f'current_{component}__lte': 100}),
                name=f'tl_prate_{component}',
            )
        )
    return constraints


class TimelinePlayer(TimelineChild):
    """One campaign's link to one shared player.

    The shared row keeps identity and the initial rating profile. This row
    keeps the present lifecycle. ``seasons_played`` is derived from club
    memberships and is not stored. A prospect has no membership. A free
    agent has a null-franchise membership. Retirement closes a membership.

    Paired status and membership writes use ``deferred_lifecycle`` so the
    cross-row check sees the finished transaction.
    """

    class Status(models.TextChoices):
        PROSPECT = 'prospect', 'prospect'
        ACTIVE = 'active', 'active'
        TAXI = 'taxi', 'taxi'
        RESERVE = 'reserve', 'reserve'
        FREE_AGENT = 'free_agent', 'free_agent'
        RETIRED = 'retired', 'retired'

    timeline = models.ForeignKey(
        Timeline,
        on_delete=models.CASCADE,
        related_name='players',
    )
    player = models.ForeignKey(
        UniversePlayer,
        on_delete=models.PROTECT,
        related_name='timeline_players',
    )
    status = models.CharField(max_length=16, choices=Status.choices)
    retired_year = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        ordering = ['timeline', 'player']
        indexes = [
            models.Index(fields=['timeline', 'status'], name='tl_player_status_idx'),
            models.Index(fields=['player'], name='tl_player_person_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['timeline', 'player'],
                name='unique_tl_player',
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=PLAYER_STATUSES),
                name='tl_player_status',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status='retired', retired_year__isnull=False)
                    | (
                        ~models.Q(status='retired')
                        & models.Q(retired_year__isnull=True)
                    )
                ),
                name='tl_player_retired',
            ),
        ]

    def __str__(self):
        return f'{self.timeline_id} {self.player_id}'

    def bound_timeline(self):
        if not self.timeline_id:
            return None
        return self.timeline

    def seasons_played(self):
        if not self.pk:
            return 0
        return (
            self.roster_memberships.exclude(franchise__isnull=True)
            .values('season_id')
            .distinct()
            .count()
        )

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        _note_deferred(self.pk)

    def extra_clean(self, errors):
        if not self.timeline_id or not self.player_id:
            return
        timeline = self.timeline
        player = self.player
        if player.release_id != timeline.release_id:
            errors['player'] = 'The player belongs to this timeline\'s release.'
        elif player.cohort == UniversePlayer.Cohort.ARCHIVE:
            errors['player'] = 'Archive players are not placed on a timeline.'
        retired = self.status == self.Status.RETIRED
        if retired != (self.retired_year is not None):
            errors['retired_year'] = (
                'Retirement records the season, and only retirement does.'
            )
        if self.retired_year is not None:
            release = timeline.release
            if self.retired_year < timeline.start_season_year or (
                self.retired_year > release.last_season_year
            ):
                errors['retired_year'] = (
                    'The retirement season is inside this timeline and its release.'
                )
            elif not TimelineSeason.objects.filter(
                timeline_id=self.timeline_id,
                season_year=self.retired_year,
            ).exists():
                errors['retired_year'] = 'The retirement season exists on this timeline.'
        previous = _previous(self)
        if previous is not None:
            if previous.timeline_id != self.timeline_id:
                errors['timeline'] = 'The timeline is fixed.'
            if previous.player_id != self.player_id:
                errors['player'] = 'The shared player is fixed.'
            if previous.retired_year is not None and (
                previous.retired_year != self.retired_year
            ):
                errors['retired_year'] = 'The retirement season is written once.'
        if self.pk and self.retired_year is not None:
            later_rating = self.season_ratings.filter(
                season__season_year__gt=self.retired_year,
            ).exists()
            later_membership = self.roster_memberships.filter(
                season__season_year__gt=self.retired_year,
            ).exists()
            later_event = self.development_events.filter(
                season__season_year__gt=self.retired_year,
            ).exists()
            if later_rating or later_membership or later_event:
                errors['retired_year'] = (
                    'A retired player has no rating, membership, or development '
                    'after the retirement season.'
                )
        if not _deferring():
            rows = []
            if self.pk:
                rows = list(self.roster_memberships.select_related('season'))
            _check_lifecycle(self, rows, errors)


class PlayerSeasonRating(TimelineChild):
    """The 33 current components for one player in one season.

    The row is the live value while the season is open and the frozen
    season-end value once that season's phase is complete. The next season
    copies it. There is no second opening-day row. Potential stays on the
    shared profile.
    """

    overlay = models.ForeignKey(
        TimelinePlayer,
        on_delete=models.CASCADE,
        related_name='season_ratings',
    )
    season = models.ForeignKey(
        TimelineSeason,
        on_delete=models.CASCADE,
        related_name='player_ratings',
    )

    class Meta:
        ordering = ['overlay', 'season']
        indexes = [
            models.Index(fields=['season'], name='tl_prate_season_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['overlay', 'season'],
                name='unique_tl_season_rating',
            ),
            *_current_rating_constraints(),
        ]

    def __str__(self):
        return f'{self.overlay_id} {self.season_id}'

    def bound_timeline(self):
        if not self.overlay_id:
            return None
        return self.overlay.timeline

    def extra_clean(self, errors):
        if not self.overlay_id or not self.season_id:
            return
        overlay = self.overlay
        season = self.season
        if overlay.timeline_id != season.timeline_id:
            errors['season'] = 'The season belongs to the player\'s timeline.'
        if overlay.retired_year is not None and season.season_year > overlay.retired_year:
            errors['season'] = 'A retired player has no rating after the retirement season.'
        if not self.pk and season.phase == Timeline.Phase.COMPLETE:
            errors['season'] = 'A completed season does not gain a rating row.'
        try:
            profile = overlay.player.rating_profile
        except PlayerRatingProfile.DoesNotExist:
            errors['overlay'] = 'The shared player has a rating profile.'
            profile = None
        if profile is not None:
            for component in PLAYER_COMPONENTS:
                name = f'current_{component}'
                if getattr(self, name) > getattr(profile, f'potential_{component}'):
                    errors[name] = 'A season rating does not exceed shared potential.'
        previous = _previous(self)
        if previous is not None:
            if previous.overlay_id != self.overlay_id or previous.season_id != self.season_id:
                errors['overlay'] = 'The player and season are fixed.'
            if season.phase == Timeline.Phase.COMPLETE:
                for component in PLAYER_COMPONENTS:
                    name = f'current_{component}'
                    if getattr(previous, name) != getattr(self, name):
                        errors[name] = 'A completed season keeps its rating.'


for _component in PLAYER_COMPONENTS:
    PlayerSeasonRating.add_to_class(
        f'current_{_component}',
        models.PositiveSmallIntegerField(),
    )


class RosterMembership(TimelineChild):
    """Club, season, position, and role for a span of weeks.

    ``position`` and ``role`` do not change. A position change, including
    one that keeps the same club, is a new row. A free agent has a null
    franchise. Week 0 is the preseason. ``reserve`` is legal from the
    roster-cut phase onward.
    """

    class Role(models.TextChoices):
        STARTER = 'starter', 'starter'
        BACKUP = 'backup', 'backup'
        TAXI = 'taxi', 'taxi'
        RESERVE = 'reserve', 'reserve'
        FREE_AGENT = 'free_agent', 'free_agent'

    class Availability(models.TextChoices):
        AVAILABLE = 'available', 'available'
        UNAVAILABLE = 'unavailable', 'unavailable'

    overlay = models.ForeignKey(
        TimelinePlayer,
        on_delete=models.CASCADE,
        related_name='roster_memberships',
    )
    season = models.ForeignKey(
        TimelineSeason,
        on_delete=models.CASCADE,
        related_name='roster_memberships',
    )
    franchise = models.ForeignKey(
        'universe.UniverseFranchise',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='timeline_roster_memberships',
    )
    position = models.CharField(max_length=4, choices=_choices(POSITIONS))
    role = models.CharField(max_length=16, choices=Role.choices)
    availability = models.CharField(max_length=16, choices=Availability.choices)
    start_week = models.PositiveSmallIntegerField()
    end_week = models.PositiveSmallIntegerField(null=True, blank=True)
    reason_ended = models.CharField(
        max_length=16,
        choices=_choices(ROSTER_CLOSE_REASONS),
        null=True,
        blank=True,
    )
    open_overlay_key = models.GeneratedField(
        expression=models.Case(
            models.When(
                end_week__isnull=True,
                then=models.F('overlay_id'),
            ),
            default=models.Value(None),
            output_field=models.BigIntegerField(null=True),
        ),
        output_field=models.BigIntegerField(null=True),
        db_persist=True,
    )

    class Meta:
        ordering = ['overlay', 'season', 'start_week']
        indexes = [
            models.Index(fields=['franchise', 'season'], name='tl_rmem_club_idx'),
            models.Index(fields=['overlay', 'season'], name='tl_rmem_player_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['open_overlay_key'],
                name='unique_tl_open_roster',
            ),
            models.CheckConstraint(
                condition=models.Q(position__in=POSITIONS),
                name='tl_membership_position',
            ),
            models.CheckConstraint(
                condition=models.Q(role__in=ROSTER_ROLES),
                name='tl_membership_role',
            ),
            models.CheckConstraint(
                condition=models.Q(availability__in=ROSTER_AVAILABILITY),
                name='tl_membership_availability',
            ),
            models.CheckConstraint(
                condition=models.Q(start_week__gte=0),
                name='tl_membership_start_week',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(end_week__isnull=True, reason_ended__isnull=True)
                    | models.Q(
                        end_week__isnull=False,
                        end_week__gte=models.F('start_week'),
                        reason_ended__in=ROSTER_CLOSE_REASONS,
                    )
                ),
                name='tl_membership_close',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(role='free_agent', franchise__isnull=True)
                    | (
                        ~models.Q(role='free_agent')
                        & models.Q(franchise__isnull=False)
                    )
                ),
                name='tl_membership_free_agent_franchise',
            ),
        ]

    def __str__(self):
        return f'{self.overlay_id} {self.season_id} {self.role}'

    def bound_timeline(self):
        if not self.overlay_id:
            return None
        return self.overlay.timeline

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        _note_deferred(self.overlay_id)

    def extra_clean(self, errors):
        if not self.overlay_id or not self.season_id:
            return
        overlay = self.overlay
        season = self.season
        timeline = overlay.timeline
        if overlay.timeline_id != season.timeline_id:
            errors['season'] = 'The season belongs to the player\'s timeline.'
        if overlay.retired_year is not None and season.season_year > overlay.retired_year:
            errors['season'] = (
                'A retired player has no membership after the retirement season.'
            )
        if not self.pk and season.phase == Timeline.Phase.COMPLETE:
            errors['season'] = 'A completed season does not gain a roster membership.'
        if self.role == self.Role.RESERVE and season.phase in RESERVE_BLOCKED_PHASES:
            errors['role'] = 'Reserve is legal from the roster-cut phase onward.'
        player = overlay.player
        allowed = {player.position}
        if player.secondary_position_1:
            allowed.add(player.secondary_position_1)
        if player.secondary_position_2:
            allowed.add(player.secondary_position_2)
        if self.position not in allowed:
            errors['position'] = (
                'The roster position is the player\'s primary position or a listed secondary.'
            )
        if self.franchise_id:
            if self.franchise.release_id != timeline.release_id:
                errors['franchise'] = 'The franchise belongs to this timeline\'s release.'
            else:
                from universe.models import FranchiseSeasonMembership
                active = FranchiseSeasonMembership.objects.filter(
                    franchise_id=self.franchise_id,
                    season_year=season.season_year,
                ).exists()
                if not active:
                    errors['franchise'] = 'The franchise is active in this season.'
        if (self.end_week is None) != (self.reason_ended is None):
            errors['reason_ended'] = (
                'A roster membership closes with a week and a reason together.'
            )
        elif self.end_week is not None and self.end_week < self.start_week:
            errors['end_week'] = 'The membership ends on or after the week it starts.'
        others = RosterMembership.objects.filter(
            overlay_id=self.overlay_id,
            season_id=self.season_id,
        )
        if self.pk:
            others = others.exclude(pk=self.pk)
        for other in others:
            if _weeks_conflict(
                self.start_week,
                self.end_week,
                other.start_week,
                other.end_week,
            ):
                errors['start_week'] = (
                    'Roster weeks for one player in a season do not overlap.'
                )
                break
        previous = _previous(self)
        if previous is not None:
            fixed = (
                'overlay_id',
                'season_id',
                'franchise_id',
                'position',
                'role',
                'start_week',
            )
            for name in fixed:
                if getattr(previous, name) != getattr(self, name):
                    errors[name] = 'This roster fact is fixed.'
            if previous.end_week is not None and (
                previous.end_week != self.end_week
                or previous.reason_ended != self.reason_ended
            ):
                errors['end_week'] = 'A closed roster membership stays closed.'
            if season.phase == Timeline.Phase.COMPLETE and (
                previous.availability != self.availability
            ):
                errors['availability'] = 'A completed season keeps availability.'
        if not _deferring():
            rows = list(overlay.roster_memberships.select_related('season'))
            if self.pk:
                rows = [row for row in rows if row.pk != self.pk]
            rows.append(self)
            _check_lifecycle(overlay, rows, errors, field='role')


class DevelopmentEvent(TimelineChild):
    """One immutable component change inside a season.

    The season rating row is the value a reader uses. These rows explain
    the path. The model does not apply the delta.
    """

    overlay = models.ForeignKey(
        TimelinePlayer,
        on_delete=models.CASCADE,
        related_name='development_events',
    )
    season = models.ForeignKey(
        TimelineSeason,
        on_delete=models.CASCADE,
        related_name='development_events',
    )
    component = models.CharField(max_length=32, choices=_choices(PLAYER_COMPONENTS))
    delta = models.SmallIntegerField()
    reason = models.CharField(max_length=16, choices=_choices(DEVELOPMENT_REASONS))
    event_index = models.PositiveIntegerField()

    class Meta:
        ordering = ['overlay', 'season', 'component', 'event_index']
        indexes = [
            models.Index(fields=['overlay', 'season'], name='tl_dev_season_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['overlay', 'season', 'component', 'event_index'],
                name='unique_tl_development',
            ),
            models.CheckConstraint(
                condition=models.Q(component__in=PLAYER_COMPONENTS),
                name='tl_dev_component',
            ),
            models.CheckConstraint(
                condition=models.Q(delta__gte=-20, delta__lte=20),
                name='tl_dev_delta',
            ),
            models.CheckConstraint(
                condition=models.Q(reason__in=DEVELOPMENT_REASONS),
                name='tl_dev_reason',
            ),
            models.CheckConstraint(
                condition=models.Q(event_index__gte=1),
                name='tl_dev_index',
            ),
        ]

    def __str__(self):
        return f'{self.overlay_id} {self.component} {self.event_index}'

    def bound_timeline(self):
        if not self.overlay_id:
            return None
        return self.overlay.timeline

    def extra_clean(self, errors):
        if not self.overlay_id or not self.season_id:
            return
        overlay = self.overlay
        season = self.season
        if overlay.timeline_id != season.timeline_id:
            errors['season'] = 'The season belongs to the player\'s timeline.'
        if overlay.retired_year is not None and season.season_year > overlay.retired_year:
            errors['season'] = (
                'A retired player has no development after the retirement season.'
            )
        if not self.pk and season.phase == Timeline.Phase.COMPLETE:
            errors['season'] = 'A completed season does not gain a development event.'
        previous = _previous(self)
        if previous is not None and _changed(previous, self, _stored_names(self)):
            errors['event_index'] = 'A development event is written once.'
