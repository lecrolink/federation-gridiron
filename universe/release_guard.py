"""Lock the parent release around an ordinary child write.

Publication, the sanctioned generators, provisional replacement, and every
ordinary ``save()`` or instance ``delete()`` of a release-owned row take
``UniverseRelease`` first, with ``select_for_update()``, inside
``transaction.atomic()``. Status is read from that locked database row.

Nested calls lock the same release again. They do not lock a child row
first, and they do not lock a second release. Queryset ``update``,
``bulk_create``, ``bulk_update``, queryset ``delete``, and SQL from outside
this application skip these methods. Sanctioned bulk writers call
``assert_release_lock_held`` after ``holding_release_lock``. This module
adds no database trigger.

SQLite accepts ``select_for_update()`` and then omits ``FOR UPDATE``. A
disposable MariaDB concurrency smoke test has to show that a second
connection actually waits. That smoke test is not part of this change.
"""

import contextvars
from contextlib import contextmanager

from django.core.exceptions import ValidationError
from django.db import models, transaction


_lock_stack = contextvars.ContextVar('universe_release_lock_stack', default=())


def locks_parent_release(method):
    """Mark a model method that holds the parent release lock."""
    method.locks_parent_release = True
    return method


def _push(release_id):
    stack = _lock_stack.get()
    if stack and release_id not in stack:
        raise RuntimeError(
            'Release content locks UniverseRelease first. '
            'A different release is already locked.'
        )
    _lock_stack.set(stack + (release_id,))


def _pop():
    _lock_stack.set(_lock_stack.get()[:-1])


def assert_release_lock_held(release_id):
    """Refuse a bulk bypass unless this transaction holds that release."""
    stack = _lock_stack.get()
    if not stack or release_id not in stack:
        raise RuntimeError(
            'This bulk writer must run while its parent UniverseRelease row is locked.'
        )


@contextmanager
def holding_release_lock(release_id=None, *, slug=None):
    """Lock one release row for the duration of the surrounding transaction."""
    from universe.models import UniverseRelease

    if (release_id is None) == (slug is None):
        raise ValueError('Lock one UniverseRelease by primary key or slug.')
    with transaction.atomic():
        queryset = UniverseRelease.objects.select_for_update()
        if slug is None:
            release = queryset.get(pk=release_id)
        else:
            release = queryset.get(slug=slug)
        _push(release.pk)
        try:
            yield release
        finally:
            _pop()


def parent_release_id(instance):
    """Return the parent release primary key without trusting cached status."""
    names = {field.name for field in instance._meta.concrete_fields}
    if 'release' in names:
        return instance.release_id
    if 'franchise' in names:
        franchise_id = instance.franchise_id
        if franchise_id is None:
            return None
        from universe.models import UniverseFranchise
        return (
            UniverseFranchise.objects.filter(pk=franchise_id)
            .values_list('release_id', flat=True)
            .first()
        )
    bound = getattr(instance, 'bound_release', None)
    if not callable(bound):
        return None
    try:
        release = instance.bound_release()
    except NotImplementedError:
        return None
    if release is None:
        return None
    return release.pk


def install_locked_release(instance, locked):
    """Point cached relations at the locked release row."""
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


def _require_draft(locked):
    from universe.models import UniverseRelease

    if locked.status == UniverseRelease.Status.PUBLISHED:
        raise ValidationError('A published release cannot be changed.')


@locks_parent_release
def save_release_child(instance, *args, **kwargs):
    """Validate and save one child while its parent release is locked."""
    release_id = parent_release_id(instance)
    if release_id is None:
        with transaction.atomic():
            instance.full_clean()
            return models.Model.save(instance, *args, **kwargs)
    with holding_release_lock(release_id) as locked:
        install_locked_release(instance, locked)
        _require_draft(locked)
        instance.full_clean()
        return models.Model.save(instance, *args, **kwargs)


def delete_release_child(instance, *args, refusal, **kwargs):
    """Delete one child while its parent release is locked."""
    release_id = parent_release_id(instance)
    if release_id is None:
        with transaction.atomic():
            return models.Model.delete(instance, *args, **kwargs)
    with holding_release_lock(release_id) as locked:
        install_locked_release(instance, locked)
        from universe.models import UniverseRelease
        if locked.status == UniverseRelease.Status.PUBLISHED:
            raise ValidationError(refusal)
        return models.Model.delete(instance, *args, **kwargs)


def child_delete(refusal):
    """Build an instance ``delete`` that locks the parent release."""

    @locks_parent_release
    def delete(self, *args, **kwargs):
        return delete_release_child(self, *args, refusal=refusal, **kwargs)

    return delete
