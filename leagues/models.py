from django.core.exceptions import ValidationError
from django.db import models


class League(models.Model):
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    tier = models.PositiveIntegerField()
    parent_league = models.ForeignKey(
        'self',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='child_leagues',
    )
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['tier', 'name']

    def __str__(self):
        return self.name

    def path_to_root(self):
        """Return leagues from this league up to the top-level parent."""
        path = [self]
        current = self
        while current.parent_league_id is not None:
            current = current.parent_league
            path.append(current)
        return path

    @property
    def root_league(self):
        path = self.path_to_root()
        return path[-1]

    def clean(self):
        super().clean()
        errors = {}

        if self.tier == 1:
            if self.parent_league_id is not None:
                errors['parent_league'] = 'Tier 1 leagues cannot have a parent league.'
        elif self.parent_league_id is None:
            errors['parent_league'] = 'Leagues above tier 1 must have a parent league.'

        if self.pk and self.parent_league_id == self.pk:
            errors['parent_league'] = 'A league cannot be its own parent.'

        if self.parent_league_id is not None:
            parent = self.parent_league
            if parent.tier != self.tier - 1:
                errors['parent_league'] = (
                    f'Parent league must be tier {self.tier - 1} '
                    f'(selected parent is tier {parent.tier}).'
                )

            if self.pk:
                ancestor = parent
                while ancestor is not None:
                    if ancestor.pk == self.pk:
                        errors['parent_league'] = (
                            'A league cannot choose one of its descendants as its parent.'
                        )
                        break
                    ancestor = ancestor.parent_league

        if self.tier == 1 and self.active:
            existing = League.objects.filter(tier=1, active=True)
            if self.pk:
                existing = existing.exclude(pk=self.pk)
            if existing.exists():
                errors['active'] = 'Only one active tier 1 league may exist.'

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class Conference(models.Model):
    league = models.ForeignKey(
        League,
        on_delete=models.CASCADE,
        related_name='conferences',
    )
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255)
    sort_order = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['league', 'sort_order', 'name']
        constraints = [
            models.UniqueConstraint(
                fields=['league', 'slug'],
                name='unique_conference_slug_per_league',
            ),
        ]

    def __str__(self):
        return f'{self.league.name} — {self.name}'


class Division(models.Model):
    conference = models.ForeignKey(
        Conference,
        on_delete=models.CASCADE,
        related_name='divisions',
    )
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255)
    sort_order = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['conference', 'sort_order', 'name']
        constraints = [
            models.UniqueConstraint(
                fields=['conference', 'slug'],
                name='unique_division_slug_per_conference',
            ),
        ]

    def __str__(self):
        return f'{self.conference.name} — {self.name}'


class Team(models.Model):
    league = models.ForeignKey(
        League,
        on_delete=models.CASCADE,
        related_name='teams',
    )
    conference = models.ForeignKey(
        Conference,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='teams',
    )
    division = models.ForeignKey(
        Division,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='teams',
    )
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255)
    city = models.CharField(max_length=255, blank=True)
    region = models.CharField(max_length=255, blank=True)
    country = models.CharField(max_length=255, blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['league', 'sort_order', 'name']
        constraints = [
            models.UniqueConstraint(
                fields=['league', 'slug'],
                name='unique_team_slug_per_league',
            ),
        ]

    def __str__(self):
        return self.name


class MovementPath(models.Model):
    class MovementType(models.TextChoices):
        PROMOTION = 'promotion', 'Promotion'
        RELEGATION = 'relegation', 'Relegation'
        LATERAL = 'lateral', 'Lateral'
        SPECIAL = 'special', 'Special'

    from_league = models.ForeignKey(
        League,
        on_delete=models.CASCADE,
        related_name='movement_paths_from',
    )
    from_team = models.ForeignKey(
        Team,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='movement_paths_from',
    )
    to_league = models.ForeignKey(
        League,
        on_delete=models.CASCADE,
        related_name='movement_paths_to',
    )
    to_team = models.ForeignKey(
        Team,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='movement_paths_to',
    )
    movement_type = models.CharField(
        max_length=20,
        choices=MovementType.choices,
    )
    active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['from_league', 'to_league', 'movement_type']

    def __str__(self):
        return f'{self.from_league.name} → {self.to_league.name} ({self.get_movement_type_display()})'
