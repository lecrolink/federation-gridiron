"""Publish one exact universe release.

The read-only evaluator reports gates and does not write. This service is
the only publication writer. It locks the release through the shared release
lock, evaluates candidate digests, and saves publication fields only after
every required gate passes. It does not modify a release child, create a
timeline, or unpublish. Ordinary child saves use the same lock.
"""

import re

from django.db import DEFAULT_DB_ALIAS
from django.utils import timezone

from timelines.models import Timeline
from universe.fingerprints import (
    SCHEME_BY_GENERATOR,
    FingerprintSchemaError,
    FingerprintSchemeError,
    release_digests,
)
from universe.models import UniverseRelease
from universe.publication import evaluate_release_publication
from universe.release_guard import holding_release_lock


_DIGEST = re.compile(r'^[0-9a-f]{64}$')
REQUIRED_GATES = (
    'horizon',
    'expansion',
    'membership',
    'quota',
    'population',
    'staff_signature',
    'history_events',
    'draft_order',
    'opening_depth',
)
PUBLICATION_FIELDS = (
    'catalog_fingerprint',
    'validation_fingerprint',
    'status',
    'published_at',
)


class PublicationRefused(Exception):
    """The release was not published and no publication field was kept."""

    def __init__(self, message, failed_gates=()):
        super().__init__(message)
        self.failed_gates = tuple(failed_gates)


class PublicationResult:
    """The outcome of one publication attempt. ``unchanged`` wrote nothing."""

    def __init__(self, outcome, release, report):
        self.outcome = outcome
        self.release_id = release.pk
        self.published_at = release.published_at
        self.catalog_fingerprint = release.catalog_fingerprint
        self.validation_fingerprint = release.validation_fingerprint
        self.report = report


def publish_universe_release(*, slug, seed, generator_version, league_id):
    """Publish one locked release, or return unchanged for an exact rerun."""
    _require_arguments(slug, seed, generator_version, league_id)
    try:
        with holding_release_lock(slug=slug, using=DEFAULT_DB_ALIAS) as release:
            _require_identity(release, seed, generator_version, league_id)
            _require_status_pair(release)
            if release.generator_version not in SCHEME_BY_GENERATOR:
                raise PublicationRefused(
                    'Publication refused: fingerprint_scheme. '
                    'The generator version has no approved fingerprint scheme.',
                    failed_gates=('fingerprint_scheme',),
                )
            if release.status == UniverseRelease.Status.PUBLISHED:
                return _unchanged_published(release)
            if Timeline.objects.filter(release=release).exists():
                raise PublicationRefused('A timeline already references this release.')
            return _publish_draft(release)
    except UniverseRelease.DoesNotExist as exc:
        raise PublicationRefused('The release does not exist.') from exc


def failed_gate_names(report):
    """Names of required gates that are not ``pass``, plus fingerprint failures."""
    names = []
    if report.cap_blockers:
        names.append('cap')
    if report.fingerprint_verification == 'unsupported':
        names.append('fingerprint_scheme')
    elif report.fingerprint_verification != 'match':
        names.append('fingerprint')
    for name in REQUIRED_GATES:
        if getattr(report, f'{name}_status') != 'pass':
            names.append(name)
    return tuple(names)


def _require_arguments(slug, seed, generator_version, league_id):
    if not isinstance(slug, str) or not slug:
        raise PublicationRefused('The release does not exist.')
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise PublicationRefused('The release seed does not match.')
    if not isinstance(generator_version, str) or not generator_version:
        raise PublicationRefused('The release generator version does not match.')
    if isinstance(league_id, bool) or not isinstance(league_id, int):
        raise PublicationRefused('The release league does not match.')


def _require_identity(release, seed, generator_version, league_id):
    if release.root_league_id != league_id:
        raise PublicationRefused('The release league does not match.')
    if release.seed != seed:
        raise PublicationRefused('The release seed does not match.')
    if release.generator_version != generator_version:
        raise PublicationRefused('The release generator version does not match.')


def _require_status_pair(release):
    draft = (
        release.status == UniverseRelease.Status.DRAFT
        and release.published_at is None
    )
    published = (
        release.status == UniverseRelease.Status.PUBLISHED
        and release.published_at is not None
        and bool(release.catalog_fingerprint)
        and bool(release.validation_fingerprint)
    )
    if not draft and not published:
        raise PublicationRefused(
            'The release status and published_at combination is invalid.'
        )


def _valid_digest(value):
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None


def _computed_digests(release):
    try:
        catalog_digest, validation_digest = release_digests(release)
    except FingerprintSchemaError as exc:
        raise PublicationRefused(
            'Publication refused: fingerprint_schema. '
            f'Fingerprint computation failed: {exc}',
            failed_gates=('fingerprint_schema',),
        ) from exc
    except FingerprintSchemeError as exc:
        raise PublicationRefused(
            'Publication refused: fingerprint_scheme. '
            'The generator version has no approved fingerprint scheme.',
            failed_gates=('fingerprint_scheme',),
        ) from exc
    if not _valid_digest(catalog_digest) or not _valid_digest(validation_digest):
        raise PublicationRefused(
            'Publication refused: fingerprint_schema. Fingerprint computation failed.',
            failed_gates=('fingerprint_schema',),
        )
    return catalog_digest, validation_digest


def _refuse_failed_report(report):
    failed = failed_gate_names(report)
    if failed or not report.publishable:
        shown = ', '.join(failed) if failed else 'publication'
        raise PublicationRefused(
            f'Publication refused: {shown}.',
            failed_gates=failed,
        )


def _evaluate(release, **kwargs):
    try:
        return evaluate_release_publication(release, **kwargs)
    except FingerprintSchemaError as exc:
        raise PublicationRefused(
            'Publication refused: fingerprint_schema. '
            f'Fingerprint computation failed: {exc}',
            failed_gates=('fingerprint_schema',),
        ) from exc
    except FingerprintSchemeError as exc:
        raise PublicationRefused(
            'Publication refused: fingerprint_scheme. '
            'The generator version has no approved fingerprint scheme.',
            failed_gates=('fingerprint_scheme',),
        ) from exc


def _publish_draft(release):
    catalog_digest, validation_digest = _computed_digests(release)
    report = _evaluate(
        release,
        candidate_fingerprints=(catalog_digest, validation_digest),
    )
    _refuse_failed_report(report)
    if (
        report.catalog_digest != catalog_digest
        or report.validation_digest != validation_digest
    ):
        raise PublicationRefused(
            'Publication refused: fingerprint_schema. Fingerprint computation failed.',
            failed_gates=('fingerprint_schema',),
        )
    release.catalog_fingerprint = catalog_digest
    release.validation_fingerprint = validation_digest
    release.status = UniverseRelease.Status.PUBLISHED
    release.published_at = timezone.now()
    release.save(update_fields=PUBLICATION_FIELDS)
    return PublicationResult('published', release, report)


def _unchanged_published(release):
    if (
        not _valid_digest(release.catalog_fingerprint)
        or not _valid_digest(release.validation_fingerprint)
    ):
        raise PublicationRefused(
            'Publication refused: fingerprint. '
            'The published fingerprints are not lowercase 64-character hex.',
            failed_gates=('fingerprint',),
        )
    stored_catalog = release.catalog_fingerprint
    stored_validation = release.validation_fingerprint
    stored_published_at = release.published_at
    catalog_digest, validation_digest = _computed_digests(release)
    if catalog_digest != stored_catalog or validation_digest != stored_validation:
        raise PublicationRefused(
            'Publication refused: fingerprint. '
            'The published fingerprints do not match the recomputed digests.',
            failed_gates=('fingerprint',),
        )
    report = _evaluate(release)
    _refuse_failed_report(report)
    if (
        release.catalog_fingerprint != stored_catalog
        or release.validation_fingerprint != stored_validation
        or release.published_at != stored_published_at
        or release.status != UniverseRelease.Status.PUBLISHED
    ):
        raise PublicationRefused(
            'The published release was not left unchanged.',
            failed_gates=('fingerprint',),
        )
    return PublicationResult('unchanged', release, report)
