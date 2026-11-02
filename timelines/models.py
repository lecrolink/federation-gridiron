"""Timeline campaigns, human participants, and the simulation-audit foundation.

Timeline rows point at shared universe identities. They do not copy the
release. Published-release data is not written here.

Deleting a timeline cascades to its own rows. Shared releases, franchises,
people, rules, and expansion events use PROTECT, so a timeline delete cannot
remove them. The account policy in the model proposal deleted a campaign
when its single owner was deleted. A timeline can later have more than one
human, so the creator and each participant use a nullable user reference
with SET_NULL. Removing an account leaves the campaign in place and does
not delete another participant's timeline.

Random draws are not stored. A later service recomputes them from the
universe seed and the stable coordinates. The timeline id, the participant,
and owner decisions are not inputs to that draw. This module does not
simulate, and it does not insert campaigns.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from universe.people import GM_PRIORITIES, UniversePlayer
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
        if self.season.directives_locked and not self.locked:
            errors['locked'] = 'A locked season locks its owner directive.'
        previous = _previous(self)
        if previous is not None and previous.locked:
            if _changed(previous, self, _stored_names(self)):
                errors['locked'] = (
                    'A directive stays as it was when the season simulation began.'
                )
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

    ``game_key`` is the stable identity until that schedule row exists.
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
        elif self.week > profile.schedule_length:
            errors['week'] = 'The week is inside that profile\'s schedule.'
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
