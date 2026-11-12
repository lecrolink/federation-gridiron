"""Shared franchise history for one universe release.

``clean()``, ``save()``, and instance ``delete()`` refuse changes to a
published release. Ordinary saves and deletes lock the release stored for
that row and read ``status`` from that locked row. An in-memory reassignment
does not choose the lock or move the row. Queryset updates, bulk operations,
and direct SQL bypass them. Sanctioned bulk writers hold the same release
lock. Constraints on these models cover
single-table structure only. The confirmed 1970–2029 schedule is release
data, checked when that release is generated and published. Opponent
pairings are the 1970–1977 project approximation and carry no week, score,
or historical chart. Week assignments are separate rows that point at
those pairings. Historical schedule templates live in
``universe.historical`` and are reference rows, not release rows.
This module adds no database trigger.
"""

from django.core.exceptions import ValidationError
from django.core.validators import validate_slug
from django.db import models

from universe.release_guard import (
    child_delete,
    holding_release_lock,
    locks_parent_release,
    save_release_child,
    write_alias,
)


ARCHIVE_SEASON_COUNT = 10


def _previous(instance):
    if not instance.pk:
        return None
    queryset = type(instance).objects
    if instance._state.db:
        queryset = queryset.using(instance._state.db)
    return queryset.filter(pk=instance.pk).first()


def _changed_fields(previous, current, field_names):
    return [
        name for name in field_names
        if getattr(previous, name) != getattr(current, name)
    ]


class UniverseRelease(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        PUBLISHED = 'published', 'Published'

    slug = models.CharField(max_length=64, unique=True)
    root_league = models.ForeignKey(
        'leagues.League',
        on_delete=models.PROTECT,
        related_name='universe_releases',
    )
    generator_version = models.CharField(max_length=64)
    seed = models.BigIntegerField()
    founding_franchise_count = models.PositiveSmallIntegerField()
    expansion_schedule_version = models.PositiveIntegerField()
    catalog_fingerprint = models.CharField(max_length=64, blank=True, default='')
    validation_fingerprint = models.CharField(max_length=64, blank=True, default='')
    first_season_year = models.PositiveSmallIntegerField()
    season_count = models.PositiveSmallIntegerField()
    last_season_year = models.PositiveSmallIntegerField()
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['slug']
        indexes = [
            models.Index(fields=['status'], name='universe_release_status_idx'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(founding_franchise_count__gte=1),
                name='universe_release_founding_positive',
            ),
            models.CheckConstraint(
                condition=models.Q(season_count__gte=1),
                name='universe_release_season_count_positive',
            ),
            models.CheckConstraint(
                condition=models.Q(
                    last_season_year__gte=models.F('first_season_year'),
                ),
                name='universe_release_season_order',
            ),
            models.CheckConstraint(
                condition=models.Q(
                    last_season_year=(
                        models.F('first_season_year') + models.F('season_count') - 1
                    ),
                ),
                name='universe_release_season_span',
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(status='published')
                    | (
                        ~models.Q(catalog_fingerprint='')
                        & ~models.Q(validation_fingerprint='')
                        & models.Q(published_at__isnull=False)
                    )
                ),
                name='universe_release_published_complete',
            ),
        ]

    def __str__(self):
        return self.slug

    def clean(self):
        super().clean()
        errors = {}
        if self.status == self.Status.PUBLISHED:
            if not self.catalog_fingerprint or not self.validation_fingerprint:
                errors['catalog_fingerprint'] = (
                    'A published release needs both fingerprints.'
                )
            if self.published_at is None:
                errors['published_at'] = 'A published release needs a publication time.'

        previous = _previous(self)
        if previous is not None and previous.status == self.Status.PUBLISHED:
            changed = _changed_fields(
                previous,
                self,
                (
                    'slug',
                    'root_league_id',
                    'generator_version',
                    'seed',
                    'founding_franchise_count',
                    'expansion_schedule_version',
                    'catalog_fingerprint',
                    'validation_fingerprint',
                    'first_season_year',
                    'season_count',
                    'last_season_year',
                    'published_at',
                    'status',
                ),
            )
            for name in changed:
                errors[name] = (
                    'Model save cannot change this field once the release is published.'
                )

        if errors:
            raise ValidationError(errors)

    @locks_parent_release
    def save(self, *args, **kwargs):
        alias = write_alias(self, args, kwargs, 2)
        self._state.db = alias
        if self.pk is None:
            self.full_clean()
            return super().save(*args, **kwargs)
        with holding_release_lock(self.pk, using=alias):
            self.full_clean()
            return super().save(*args, **kwargs)

    @locks_parent_release
    def delete(self, *args, **kwargs):
        alias = write_alias(self, args, kwargs, 0)
        if self.pk is None:
            if self.status == self.Status.PUBLISHED:
                raise ValidationError('A published release cannot be deleted.')
            return super().delete(*args, **kwargs)
        with holding_release_lock(self.pk, using=alias) as locked:
            if locked.status == self.Status.PUBLISHED:
                raise ValidationError('A published release cannot be deleted.')
            self._state.db = alias
            return super().delete(*args, **kwargs)


class UniverseFranchise(models.Model):
    class Origin(models.TextChoices):
        FOUNDING = 'founding', 'Founding'
        EXPANSION = 'expansion', 'Expansion'

    release = models.ForeignKey(
        UniverseRelease,
        on_delete=models.CASCADE,
        related_name='franchises',
    )
    catalog_team = models.ForeignKey(
        'leagues.Team',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='universe_franchises',
    )
    history_predecessor = models.ForeignKey(
        'self',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='history_successors',
    )
    placeholder_slug = models.SlugField(max_length=64)
    display_name = models.CharField(max_length=255)
    origin = models.CharField(max_length=16, choices=Origin.choices)
    first_active_season = models.PositiveSmallIntegerField()
    final_active_season = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        ordering = ['release', 'placeholder_slug']
        indexes = [
            models.Index(
                fields=['release', 'first_active_season'],
                name='universe_franchise_debut_idx',
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['release', 'placeholder_slug'],
                name='unique_franchise_slug_per_release',
            ),
            models.UniqueConstraint(
                fields=['release', 'catalog_team'],
                name='unique_franchise_catalog_team',
            ),
            models.CheckConstraint(
                condition=models.Q(origin__in=['founding', 'expansion']),
                name='universe_franchise_origin',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(final_active_season__isnull=True)
                    | models.Q(final_active_season__gte=models.F('first_active_season'))
                ),
                name='universe_franchise_final_season',
            ),
        ]

    def __str__(self):
        return self.placeholder_slug

    def clean(self):
        super().clean()
        errors = {}
        if self.history_predecessor_id:
            if self.pk and self.history_predecessor_id == self.pk:
                errors['history_predecessor'] = 'A franchise cannot precede itself.'
            else:
                predecessor = self.history_predecessor
                if predecessor.release_id != self.release_id:
                    errors['history_predecessor'] = (
                        'The predecessor must belong to the same release.'
                    )
                else:
                    seen = set()
                    current = predecessor
                    while current is not None:
                        if (self.pk and current.pk == self.pk) or current.pk in seen:
                            errors['history_predecessor'] = (
                                'Predecessor links cannot form a loop.'
                            )
                            break
                        seen.add(current.pk)
                        current = current.history_predecessor

        if self.release_id:
            release = self.release
            if (
                self.first_active_season < release.first_season_year
                or self.first_active_season > release.last_season_year
            ):
                errors['first_active_season'] = (
                    'The first active season must fall inside the release.'
                )
            if self.final_active_season is not None and (
                self.final_active_season < release.first_season_year
                or self.final_active_season > release.last_season_year
            ):
                errors['final_active_season'] = (
                    'The final active season must fall inside the release.'
                )
            if (
                self.origin == self.Origin.FOUNDING
                and self.first_active_season != release.first_season_year
            ):
                errors['origin'] = (
                    'A founding franchise begins in the release\'s first season.'
                )
            elif (
                self.origin == self.Origin.EXPANSION
                and self.first_active_season <= release.first_season_year
            ):
                errors['origin'] = (
                    'An expansion franchise begins after the release\'s first season.'
                )
            if self.catalog_team_id and self.catalog_team.league_id != release.root_league_id:
                errors['catalog_team'] = (
                    'The catalog team must belong to this release\'s root league.'
                )

        if self.release_id and self.release.status == UniverseRelease.Status.PUBLISHED:
            if not self.pk:
                errors['release'] = 'A published release cannot gain a franchise.'
            else:
                previous = _previous(self)
                if previous is not None:
                    changed = _changed_fields(
                        previous,
                        self,
                        (
                            'release_id',
                            'placeholder_slug',
                            'display_name',
                            'origin',
                            'first_active_season',
                            'final_active_season',
                            'history_predecessor_id',
                        ),
                    )
                    for name in changed:
                        errors[name] = (
                            'Model save cannot change this field once the release is published.'
                        )
                    if (
                        previous.catalog_team_id is not None
                        and self.catalog_team_id != previous.catalog_team_id
                    ):
                        errors['catalog_team'] = (
                            'A catalog link cannot change once it has been set.'
                        )

        if errors:
            raise ValidationError(errors)

    save = save_release_child
    delete = child_delete('A franchise on a published release cannot be deleted.')


class ProvisionalFranchiseName(models.Model):
    """Fictional city and nickname for one placeholder slug.

    The row is keyed by the release and ``placeholder_slug``. It does not
    point at ``UniverseFranchise``, so deleting and recreating a franchise
    cannot cascade into a name that has already been replaced.
    """

    class Status(models.TextChoices):
        PROVISIONAL = 'provisional', 'Provisional'
        REPLACED = 'replaced', 'Replaced'

    release = models.ForeignKey(
        UniverseRelease,
        on_delete=models.CASCADE,
        related_name='provisional_franchise_names',
    )
    placeholder_slug = models.SlugField(max_length=64)
    city = models.CharField(max_length=255)
    city_status = models.CharField(max_length=16, choices=Status.choices)
    nickname = models.CharField(max_length=255)
    nickname_status = models.CharField(max_length=16, choices=Status.choices)

    class Meta:
        ordering = ['release', 'placeholder_slug']
        constraints = [
            models.UniqueConstraint(
                fields=['release', 'placeholder_slug'],
                name='unique_provisional_name_slug',
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(city='')
                    & ~models.Q(nickname='')
                    & ~models.Q(placeholder_slug='')
                ),
                name='provisional_name_nonempty',
            ),
            models.CheckConstraint(
                condition=models.Q(
                    city_status__in=['provisional', 'replaced'],
                    nickname_status__in=['provisional', 'replaced'],
                ),
                name='provisional_name_status',
            ),
        ]

    def __str__(self):
        return f'{self.placeholder_slug} {self.city} {self.nickname}'

    def clean(self):
        super().clean()
        errors = {}
        _require_slug(self.placeholder_slug, 'placeholder_slug', errors)
        if not self.city:
            errors['city'] = 'Enter a nonempty city.'
        if not self.nickname:
            errors['nickname'] = 'Enter a nonempty nickname.'
        if self.release_id:
            previous = _previous(self)
            if previous is not None:
                for value_field, status_field in (
                    ('city', 'city_status'),
                    ('nickname', 'nickname_status'),
                ):
                    if getattr(previous, status_field) != self.Status.REPLACED:
                        continue
                    if (
                        getattr(self, value_field) != getattr(previous, value_field)
                        or getattr(self, status_field) != self.Status.REPLACED
                    ):
                        errors[value_field] = 'A replaced city or nickname is kept.'
            siblings = type(self).objects.filter(release_id=self.release_id)
            if self.pk:
                siblings = siblings.exclude(pk=self.pk)
            for other in siblings:
                if self.city and other.city == self.city:
                    errors['city'] = (
                        'That city is already used by another franchise in this release.'
                    )
                if self.nickname and other.nickname == self.nickname:
                    errors['nickname'] = (
                        'That nickname is already used by another franchise '
                        'in this release.'
                    )
            _reject_published_child(self, self.release, errors, new_row_field='release')
        if errors:
            raise ValidationError(errors)

    save = save_release_child
    delete = child_delete(
        'A provisional name on a published release cannot be deleted.'
    )


class FranchiseSeasonMembership(models.Model):
    franchise = models.ForeignKey(
        UniverseFranchise,
        on_delete=models.CASCADE,
        related_name='memberships',
    )
    season_year = models.PositiveSmallIntegerField()
    conference_slug = models.CharField(max_length=32)
    division_slug = models.CharField(max_length=32)
    slot_order = models.PositiveSmallIntegerField()
    owner_selectable = models.BooleanField(
        default=True,
        help_text=(
            'Whether a human owner may control this franchise during this '
            'season. This is not eligibility to start a new timeline. False '
            'marks a future bot-only season; none are scheduled.'
        ),
    )

    class Meta:
        ordering = ['franchise', 'season_year']
        indexes = [
            models.Index(
                fields=['season_year', 'conference_slug', 'division_slug', 'slot_order'],
                name='universe_membership_align_idx',
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['franchise', 'season_year'],
                name='unique_membership_franchise_year',
            ),
            models.CheckConstraint(
                condition=models.Q(slot_order__gte=1),
                name='universe_membership_slot_positive',
            ),
            models.CheckConstraint(
                condition=~models.Q(conference_slug=''),
                name='universe_membership_conference_nonempty',
            ),
            models.CheckConstraint(
                condition=~models.Q(division_slug=''),
                name='universe_membership_division_nonempty',
            ),
        ]

    def __str__(self):
        return f'{self.franchise} {self.season_year}'

    def clean(self):
        super().clean()
        errors = {}
        if self.franchise_id:
            franchise = self.franchise
            release = franchise.release
            _require_slug(self.conference_slug, 'conference_slug', errors)
            _require_slug(self.division_slug, 'division_slug', errors)
            last_year = franchise.final_active_season or release.last_season_year
            if self.season_year < franchise.first_active_season or self.season_year > last_year:
                errors['season_year'] = (
                    'The season must fall inside this franchise\'s active span.'
                )
            elif (
                self.season_year < release.first_season_year
                or self.season_year > release.last_season_year
            ):
                errors['season_year'] = (
                    'The season must fall inside the release\'s playable seasons.'
                )
            clash = FranchiseSeasonMembership.objects.filter(
                franchise__release_id=franchise.release_id,
                season_year=self.season_year,
                conference_slug=self.conference_slug,
                division_slug=self.division_slug,
                slot_order=self.slot_order,
            )
            if self.pk:
                clash = clash.exclude(pk=self.pk)
            if clash.exists():
                errors['slot_order'] = (
                    'That conference, division, and slot are already used in this season.'
                )
            _reject_published_child(self, release, errors)

        if errors:
            raise ValidationError(errors)

    save = save_release_child
    delete = child_delete('A membership on a published release cannot be deleted.')


class FranchiseHistoryEvent(models.Model):
    class Kind(models.TextChoices):
        RENAME = 'rename', 'Rename'
        RELOCATION = 'relocation', 'Relocation'
        REORGANIZATION = 'reorganization', 'Reorganization'

    franchise = models.ForeignKey(
        UniverseFranchise,
        on_delete=models.CASCADE,
        related_name='history_events',
    )
    season_year = models.PositiveSmallIntegerField()
    kind = models.CharField(max_length=32, choices=Kind.choices)
    previous_name = models.CharField(max_length=255)
    new_name = models.CharField(max_length=255)
    city = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['franchise', 'season_year', 'kind']
        indexes = [
            models.Index(
                fields=['franchise', 'season_year'],
                name='universe_history_year_idx',
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['franchise', 'season_year', 'kind'],
                name='unique_history_event_per_year',
            ),
        ]

    def __str__(self):
        return f'{self.franchise} {self.season_year} {self.kind}'

    def clean(self):
        super().clean()
        errors = {}
        if self.franchise_id:
            franchise = self.franchise
            release = franchise.release
            if (
                self.season_year < release.first_season_year
                or self.season_year > release.last_season_year
            ):
                errors['season_year'] = (
                    'The event year must fall inside the release\'s playable seasons.'
                )
            elif self.season_year < franchise.first_active_season or (
                franchise.final_active_season is not None
                and self.season_year > franchise.final_active_season
            ):
                errors['season_year'] = (
                    'The event year must fall inside this franchise\'s active span.'
                )
            _reject_published_child(self, release, errors)

        if errors:
            raise ValidationError(errors)

    save = save_release_child
    delete = child_delete(
        'A history event on a published release cannot be deleted.'
    )


class HistoricalStanding(models.Model):
    franchise = models.ForeignKey(
        UniverseFranchise,
        on_delete=models.CASCADE,
        related_name='historical_standings',
    )
    season_year = models.PositiveSmallIntegerField()
    wins = models.PositiveSmallIntegerField()
    losses = models.PositiveSmallIntegerField()
    ties = models.PositiveSmallIntegerField()
    draft_order = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        ordering = ['franchise', 'season_year']
        indexes = [
            models.Index(
                fields=['season_year', 'draft_order'],
                name='universe_standing_draft_idx',
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['franchise', 'season_year'],
                name='unique_standing_franchise_year',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(draft_order__isnull=True)
                    | models.Q(draft_order__gte=1)
                ),
                name='universe_standing_draft_order_positive',
            ),
        ]

    def __str__(self):
        return f'{self.franchise} {self.season_year}'

    def clean(self):
        super().clean()
        errors = {}
        if self.franchise_id:
            franchise = self.franchise
            release = franchise.release
            archive_last = release.first_season_year - 1
            archive_first = release.first_season_year - ARCHIVE_SEASON_COUNT
            if self.season_year < archive_first or self.season_year > archive_last:
                errors['season_year'] = (
                    'Historical seasons are the ten years immediately before the release begins.'
                )
            if franchise.origin != UniverseFranchise.Origin.FOUNDING:
                errors['franchise'] = (
                    'Historical standings belong to founding franchises only.'
                )
            if self.draft_order is not None and self.season_year != archive_last:
                errors['draft_order'] = (
                    'Draft order is recorded only for the last archive season.'
                )
            elif self.draft_order is not None:
                clash = HistoricalStanding.objects.filter(
                    franchise__release_id=franchise.release_id,
                    season_year=self.season_year,
                    draft_order=self.draft_order,
                )
                if self.pk:
                    clash = clash.exclude(pk=self.pk)
                if clash.exists():
                    errors['draft_order'] = (
                        'That draft order is already used in this release and season.'
                    )
            _reject_published_child(self, franchise.release, errors)

        if errors:
            raise ValidationError(errors)

    save = save_release_child
    delete = child_delete(
        'A historical standing on a published release cannot be deleted.'
    )


class OpponentPairing(models.Model):
    """One regular-season opponent pairing for 1970–1977, 1978–1989, or 1990–1994.

    These spans are project schedules built from membership, division role,
    slot order, and season. None is a named-club NFL chart, and a row
    does not store a week, a bye, a kickoff, or a score.
    ``OpponentWeekAssignment`` points at a 1970–1977 row and does not change
    it. A 1978–1994 row has no week assignment.

    ``matrix_interconference`` counts only games between the 13 non-swing
    clubs in 1970–1977. In 1976 and 1977 that matrix has 27 games.
    ``swing_meeting`` is the one game between the two expansion swing clubs
    and is outside the matrix, so those seasons have 28 interconference
    games. ``interconference`` is the cyclic cross-conference game for
    1978–1994. It is not part of that matrix. 1990–1994 continues the
    same formula, so 1992–1994 repeat earlier opponent sets.
    """

    class Kind(models.TextChoices):
        DIVISION = 'division', 'Division'
        INTRACONFERENCE = 'intraconference', 'Intraconference'
        INTERCONFERENCE = 'interconference', 'Interconference'
        MATRIX_INTERCONFERENCE = 'matrix_interconference', 'Matrix interconference'
        SWING_CONFERENCE = 'swing_conference', 'Swing conference'
        SWING_MEETING = 'swing_meeting', 'Swing meeting'

    release = models.ForeignKey(
        UniverseRelease,
        on_delete=models.CASCADE,
        related_name='opponent_pairings',
    )
    season_year = models.PositiveSmallIntegerField()
    home_franchise = models.ForeignKey(
        UniverseFranchise,
        on_delete=models.PROTECT,
        related_name='home_opponent_pairings',
    )
    away_franchise = models.ForeignKey(
        UniverseFranchise,
        on_delete=models.PROTECT,
        related_name='away_opponent_pairings',
    )
    kind = models.CharField(max_length=32, choices=Kind.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['release', 'season_year', 'home_franchise', 'away_franchise']
        indexes = [
            models.Index(
                fields=['release', 'season_year'],
                name='universe_opponent_season_idx',
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['release', 'season_year', 'home_franchise', 'away_franchise'],
                name='unique_opponent_pairing_directed',
            ),
            models.CheckConstraint(
                condition=~models.Q(home_franchise=models.F('away_franchise')),
                name='universe_opponent_distinct_clubs',
            ),
            models.CheckConstraint(
                condition=models.Q(kind__in=[
                    'division',
                    'intraconference',
                    'interconference',
                    'matrix_interconference',
                    'swing_conference',
                    'swing_meeting',
                ]),
                name='universe_opponent_kind',
            ),
        ]

    def __str__(self):
        return f'{self.season_year} {self.home_franchise} vs {self.away_franchise}'

    def clean(self):
        super().clean()
        errors = {}
        if self.home_franchise_id and self.home_franchise_id == self.away_franchise_id:
            errors['away_franchise'] = 'The home and away clubs differ.'
        release = None
        if self.release_id:
            release = self.release
            if (
                self.season_year < release.first_season_year
                or self.season_year > release.last_season_year
            ):
                errors['season_year'] = 'The season stays inside the release.'
        for label, franchise in (
            ('home_franchise', getattr(self, 'home_franchise', None) if self.home_franchise_id else None),
            ('away_franchise', getattr(self, 'away_franchise', None) if self.away_franchise_id else None),
        ):
            if franchise is None or release is None:
                continue
            if franchise.release_id != release.pk:
                errors[label] = 'The club belongs to this release.'
        if release is not None:
            _reject_published_child(self, release, errors, new_row_field='release')
        if errors:
            raise ValidationError(errors)

    save = save_release_child
    delete = child_delete(
        'An opponent pairing on a published release cannot be deleted.'
    )


class OpponentWeekAssignment(models.Model):
    """The project week for one stored opponent pairing.

    1970–1977 seasons have 14 weeks and no byes. 1978–1994 seasons have 16
    weeks and no byes. This row points at an ``OpponentPairing`` and does
    not copy or change that pairing's clubs, kind, or season. It is not a
    historical NFL week and it stores no kickoff.
    """

    release = models.ForeignKey(
        UniverseRelease,
        on_delete=models.CASCADE,
        related_name='opponent_week_assignments',
    )
    pairing = models.OneToOneField(
        OpponentPairing,
        on_delete=models.PROTECT,
        related_name='week_assignment',
    )
    week = models.PositiveSmallIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['release', 'week', 'pairing']
        indexes = [
            models.Index(
                fields=['release', 'week'],
                name='universe_opponent_week_idx',
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(week__gte=1, week__lte=16),
                name='universe_opponent_week_range',
            ),
        ]

    def __str__(self):
        return f'week {self.week} pairing {self.pairing_id}'

    def clean(self):
        super().clean()
        errors = {}
        if self.week is None or not 1 <= self.week <= 16:
            errors['week'] = 'The week is from 1 through 16.'
        release = self.release if self.release_id else None
        pairing = self.pairing if self.pairing_id else None
        if pairing is not None and release is not None and pairing.release_id != release.pk:
            errors['pairing'] = 'The pairing belongs to this release.'
        if (
            pairing is not None
            and release is not None
            and 'week' not in errors
            and 'pairing' not in errors
        ):
            clubs = (pairing.home_franchise_id, pairing.away_franchise_id)
            clash = OpponentWeekAssignment.objects.filter(
                release_id=release.pk,
                week=self.week,
                pairing__season_year=pairing.season_year,
            ).filter(
                models.Q(pairing__home_franchise_id__in=clubs)
                | models.Q(pairing__away_franchise_id__in=clubs)
            )
            if self.pk:
                clash = clash.exclude(pk=self.pk)
            if clash.exists():
                errors['week'] = 'That club already plays in this week.'
        if release is not None:
            _reject_published_child(self, release, errors, new_row_field='release')
        if errors:
            raise ValidationError(errors)

    save = save_release_child
    delete = child_delete(
        'An opponent week assignment on a published release cannot be deleted.'
    )


def _reject_published_child(instance, release, errors, new_row_field='franchise'):
    if release.status != UniverseRelease.Status.PUBLISHED:
        return
    if not instance.pk:
        errors[new_row_field] = 'A published release cannot gain new rows.'
        return
    previous = _previous(instance)
    if previous is None:
        return
    changed = _changed_fields(
        previous,
        instance,
        [field.attname for field in instance._meta.concrete_fields if field.attname != 'id'],
    )
    for name in changed:
        errors[name] = (
            'Model save cannot change this field once the release is published.'
        )


def _require_slug(value, field_name, errors):
    if not value:
        errors[field_name] = 'Enter a nonempty slug.'
        return
    try:
        validate_slug(value)
    except ValidationError:
        errors[field_name] = 'Enter a nonempty slug.'


from universe import historical as _historical  # noqa: E402,F401
from universe import people as _people  # noqa: E402,F401
from universe import rules as _rules  # noqa: E402,F401
