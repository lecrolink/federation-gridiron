"""Lock the persisted parent release around an ordinary child write.

Publication, the sanctioned generators, provisional replacement, and every
ordinary ``save()`` or instance ``delete()`` of a release-owned row take one
``UniverseRelease`` first, with ``select_for_update()``, inside
``transaction.atomic()`` on the database alias that write would use. Status
is read from that locked database row.

An existing row resolves its owner from the stored primary key and the
stored relationship chain. The helper locks that release, confirms the
stored owner is unchanged, and refuses when the in-memory owner differs. It
does not lock the proposed release, and it does not move the row. A new row
locks the release it proposes to join. Instance ``delete()`` uses the stored
owner and ignores a dirty in-memory parent.

The held-lock record is ``(database alias, release primary key)``. Nested
calls may lock that same pair again. They do not lock a child row first, and
they do not lock a second release or the same primary key on another alias.
Queryset ``update``, ``bulk_create``, ``bulk_update``, queryset ``delete``,
and SQL from outside this application skip these methods. Sanctioned bulk
writers call ``assert_release_lock_held`` with the same alias after
``holding_release_lock``. This module adds no database trigger.

SQLite accepts ``select_for_update()`` and then omits ``FOR UPDATE``. A
disposable MariaDB concurrency smoke test has to show that a second
connection actually waits. That smoke test is not part of this change.
"""

import contextvars
from contextlib import contextmanager

from django.core.exceptions import ValidationError
from django.db import DEFAULT_DB_ALIAS, models, router, transaction


_lock_stack = contextvars.ContextVar('universe_release_lock_stack', default=())

OWNERSHIP_MESSAGE = 'A release-owned row cannot change its release.'
PUBLISHED_MESSAGE = 'A published release cannot be changed.'

# Staff posts are owned by the person, never by the franchise or college on
# the same row. Every other indirect child has one of these parents, and the
# parent itself stores release_id. Player, profile, and event are checked
# before franchise so a row that also points at a franchise keeps the chain
# its bound_release() method uses.
_PARENT_FIELDS = (
    'player',
    'gm',
    'coach',
    'profile',
    'event',
    'assignment',
    'franchise',
)


def locks_parent_release(method):
    """Mark a model method that holds the parent release lock."""
    method.locks_parent_release = True
    return method


def database_alias(instance=None, using=None):
    """Return the alias Django would use for this write."""
    if using is not None:
        return using
    if instance is not None:
        return router.db_for_write(instance.__class__, instance=instance)
    return DEFAULT_DB_ALIAS


def write_alias(instance, args, kwargs, position):
    """Read the explicit ``using`` argument from ``save`` or ``delete``."""
    if 'using' in kwargs:
        explicit = kwargs['using']
    elif len(args) > position:
        explicit = args[position]
    else:
        explicit = None
    return database_alias(instance, explicit)


def _push(key):
    stack = _lock_stack.get()
    if stack and key not in stack:
        raise RuntimeError(
            'Release content locks UniverseRelease first. '
            'A different release or database is already locked.'
        )
    _lock_stack.set(stack + (key,))


def _pop():
    _lock_stack.set(_lock_stack.get()[:-1])


def assert_release_lock_held(release_id, using=None):
    """Refuse a bulk bypass unless this context holds that alias and release."""
    alias = DEFAULT_DB_ALIAS if using is None else using
    stack = _lock_stack.get()
    if not stack or (alias, release_id) not in stack:
        raise RuntimeError(
            'This bulk writer must run while its parent UniverseRelease row is locked.'
        )


@contextmanager
def holding_release_lock(release_id=None, *, slug=None, using=None):
    """Lock one release row on ``using`` for the surrounding transaction."""
    from universe.models import UniverseRelease

    if (release_id is None) == (slug is None):
        raise ValueError('Lock one UniverseRelease by primary key or slug.')
    alias = DEFAULT_DB_ALIAS if using is None else using
    with transaction.atomic(using=alias):
        # Set the alias on this queryset. Cloning with ``using()`` would drop
        # a ``get`` wrapper installed by tests on the manager's return value.
        queryset = UniverseRelease.objects.select_for_update()
        queryset._db = alias
        if slug is None:
            release = queryset.get(pk=release_id)
        else:
            release = queryset.get(slug=slug)
        _push((alias, release.pk))
        try:
            yield release
        finally:
            _pop()


def _field_names(model):
    return {field.name for field in model._meta.concrete_fields}


def _ownership_chain(model):
    """Return the stored relationship that owns this model, if it has one."""
    names = _field_names(model)
    if 'release' in names:
        return ('release',)
    if 'gm' in names and 'coach' in names:
        return ('gm', 'coach')
    for name in _PARENT_FIELDS:
        if name in names:
            return (name,)
    return ()


def _row_exists(instance, alias):
    if not instance.pk:
        return False
    return type(instance).objects.using(alias).filter(pk=instance.pk).exists()


def persisted_release_id(instance, using):
    """Return the release stored for this primary key, ignoring memory."""
    if not instance.pk:
        return None
    model = type(instance)
    chain = _ownership_chain(model)
    if not chain:
        return None
    queryset = model.objects.using(using).filter(pk=instance.pk)
    if chain == ('release',):
        return queryset.values_list('release_id', flat=True).first()
    if chain == ('gm', 'coach'):
        row = queryset.values(
            'gm_id',
            'gm__release_id',
            'coach_id',
            'coach__release_id',
        ).first()
        if row is None:
            return None
        if row['gm_id']:
            return row['gm__release_id']
        if row['coach_id']:
            return row['coach__release_id']
        return None
    return queryset.values_list(f'{chain[0]}__release_id', flat=True).first()


def _parent_release_id(instance, name, alias):
    """Return the release a parent proposes, including a dirty cached parent."""
    field = instance._meta.get_field(name)
    current_id = getattr(instance, field.attname)
    if not current_id:
        return None
    if field.is_cached(instance):
        cached = field.get_cached_value(instance)
        if getattr(cached, 'pk', None) == current_id and hasattr(cached, 'release_id'):
            return cached.release_id
    parent_model = field.remote_field.model
    return (
        parent_model.objects.using(alias)
        .filter(pk=current_id)
        .values_list('release_id', flat=True)
        .first()
    )


def proposed_release_id(instance, using):
    """Return the release the in-memory instance would write."""
    chain = _ownership_chain(type(instance))
    if not chain:
        return None
    if chain == ('release',):
        return instance.release_id
    if chain == ('gm', 'coach'):
        if instance.gm_id:
            return _parent_release_id(instance, 'gm', using)
        if instance.coach_id:
            return _parent_release_id(instance, 'coach', using)
        return None
    return _parent_release_id(instance, chain[0], using)


def parent_release_id(instance, using=None):
    """Return the release a not-yet-stored bulk row proposes."""
    alias = database_alias(instance, using)
    return proposed_release_id(instance, alias)


def install_locked_release(instance, locked):
    """Point cached relations at the locked release row when the id matches.

    This refreshes ``status`` on objects already owned by that release. It
    does not assign a different release, and it does not save any object.
    """
    stack = [instance]
    seen = set()
    while stack:
        obj = stack.pop()
        if obj is None:
            continue
        marker = id(obj)
        if marker in seen:
            continue
        seen.add(marker)
        if getattr(obj, 'release_id', None) == locked.pk:
            obj.release = locked
        cache = getattr(getattr(obj, '_state', None), 'fields_cache', None)
        if not cache:
            continue
        for value in list(cache.values()):
            if value is not None and hasattr(value, '_meta'):
                stack.append(value)


def _prepare_locked_instance(instance, locked, alias):
    """Drop a parent cache that no longer matches the foreign key.

    ``full_clean()`` then refetches that parent. A cache whose primary key
    still matches is left in place, and ``install_locked_release`` may
    refresh its release object. Neither step writes the parent.
    """
    instance._state.db = alias
    for field in instance._meta.concrete_fields:
        if not field.is_relation or not field.is_cached(instance):
            continue
        cached = field.get_cached_value(instance)
        current_id = getattr(instance, field.attname)
        if getattr(cached, 'pk', None) != current_id:
            field.delete_cached_value(instance)
    install_locked_release(instance, locked)


def _require_draft(locked):
    from universe.models import UniverseRelease

    if locked.status == UniverseRelease.Status.PUBLISHED:
        raise ValidationError(PUBLISHED_MESSAGE)


def _reject_ownership_change(current, expected):
    if current != expected:
        raise ValidationError(OWNERSHIP_MESSAGE)


@locks_parent_release
def save_release_child(instance, *args, **kwargs):
    """Validate and save one child while its owning release stays locked."""
    alias = write_alias(instance, args, kwargs, 2)
    if _row_exists(instance, alias):
        return _save_existing_child(instance, alias, args, kwargs)
    return _save_new_child(instance, alias, args, kwargs)


def _save_existing_child(instance, alias, args, kwargs):
    persisted = persisted_release_id(instance, alias)
    if persisted is None:
        raise ValidationError(OWNERSHIP_MESSAGE)
    with holding_release_lock(persisted, using=alias) as locked:
        _reject_ownership_change(persisted_release_id(instance, alias), persisted)
        _reject_ownership_change(proposed_release_id(instance, alias), persisted)
        _require_draft(locked)
        _prepare_locked_instance(instance, locked, alias)
        instance.full_clean()
        _reject_ownership_change(persisted_release_id(instance, alias), persisted)
        _reject_ownership_change(proposed_release_id(instance, alias), persisted)
        return models.Model.save(instance, *args, **kwargs)


def _save_new_child(instance, alias, args, kwargs):
    proposed = proposed_release_id(instance, alias)
    if proposed is None:
        with transaction.atomic(using=alias):
            instance._state.db = alias
            instance.full_clean()
            return models.Model.save(instance, *args, **kwargs)
    with holding_release_lock(proposed, using=alias) as locked:
        if _row_exists(instance, alias):
            raise ValidationError(OWNERSHIP_MESSAGE)
        _reject_ownership_change(proposed_release_id(instance, alias), proposed)
        _require_draft(locked)
        _prepare_locked_instance(instance, locked, alias)
        instance.full_clean()
        _reject_ownership_change(proposed_release_id(instance, alias), proposed)
        return models.Model.save(instance, *args, **kwargs)


def delete_release_child(instance, *args, refusal, **kwargs):
    """Delete one child while its stored owning release is locked."""
    alias = write_alias(instance, args, kwargs, 0)
    if not _row_exists(instance, alias):
        with transaction.atomic(using=alias):
            instance._state.db = alias
            return models.Model.delete(instance, *args, **kwargs)
    persisted = persisted_release_id(instance, alias)
    if persisted is None:
        raise ValidationError(OWNERSHIP_MESSAGE)
    with holding_release_lock(persisted, using=alias) as locked:
        _reject_ownership_change(persisted_release_id(instance, alias), persisted)
        from universe.models import UniverseRelease
        if locked.status == UniverseRelease.Status.PUBLISHED:
            raise ValidationError(refusal)
        instance._state.db = alias
        return models.Model.delete(instance, *args, **kwargs)


def child_delete(refusal):
    """Build an instance ``delete`` that locks the persisted parent release."""

    @locks_parent_release
    def delete(self, *args, **kwargs):
        return delete_release_child(self, *args, refusal=refusal, **kwargs)

    return delete
