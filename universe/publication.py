"""Read-only check of whether a draft release meets publication rules.

Cap-on profiles with a null cap_limit are blockers. Fingerprints are
verified only for generator version fg-founding-2, using catalog-sha256-v1
and validation-sha256-v1. This module does not save a digest, change
publication, or create a timeline.
"""

from typing import NamedTuple

from universe.fingerprints import (
    SCHEME_BY_GENERATOR,
    FingerprintSchemeError,
    release_digests,
)
from universe.rules import CAP_STATUS


CAP_ON = 'on'
if CAP_ON not in CAP_STATUS:
    raise RuntimeError('The stored cap-on status is missing.')

SMOKE_FINGERPRINT_PLACEHOLDERS = frozenset((
    'smoke-catalog-fingerprint',
    'smoke-validation-fingerprint',
))
FINGERPRINT_FIELDS = ('catalog_fingerprint', 'validation_fingerprint')


class PublicationEvaluation(NamedTuple):
    """A report. It is not a saved publication."""

    cap_blockers: tuple
    fingerprint_scheme: tuple
    catalog_digest: object
    validation_digest: object
    catalog_digest_match: object
    validation_digest_match: object
    fingerprint_verification: str
    unverified_placeholder_fields: tuple
    meets_publication_requirements: bool
    messages: tuple


def evaluate_release_publication(release):
    """Report publication blockers. Do not save the release or its children."""
    cap_blockers = tuple(
        profile.profile_key
        for profile in release.era_profiles.order_by('first_year', 'profile_key')
        if profile.cap_status == CAP_ON and profile.cap_limit is None
    )
    placeholders = tuple(
        field
        for field in FINGERPRINT_FIELDS
        if getattr(release, field) in SMOKE_FINGERPRINT_PLACEHOLDERS
    )
    messages = [
        f'{key} blocks publication because its cap is on and cap_limit is null.'
        for key in cap_blockers
    ]
    scheme = SCHEME_BY_GENERATOR.get(release.generator_version)
    if scheme is None:
        messages.append(
            'Fingerprint scheme is unavailable for generator version '
            f'{release.generator_version}.'
        )
        for field in placeholders:
            messages.append(
                f'{field} is an unverified placeholder. The evaluator did not replace it.'
            )
        return PublicationEvaluation(
            cap_blockers=cap_blockers,
            fingerprint_scheme=(),
            catalog_digest=None,
            validation_digest=None,
            catalog_digest_match=None,
            validation_digest_match=None,
            fingerprint_verification='unsupported',
            unverified_placeholder_fields=placeholders,
            meets_publication_requirements=False,
            messages=tuple(messages),
        )
    try:
        catalog_digest, validation_digest = release_digests(release)
    except FingerprintSchemeError as exc:
        messages.append(str(exc))
        return PublicationEvaluation(
            cap_blockers=cap_blockers,
            fingerprint_scheme=(),
            catalog_digest=None,
            validation_digest=None,
            catalog_digest_match=None,
            validation_digest_match=None,
            fingerprint_verification='unsupported',
            unverified_placeholder_fields=placeholders,
            meets_publication_requirements=False,
            messages=tuple(messages),
        )
    catalog_match = release.catalog_fingerprint == catalog_digest
    validation_match = release.validation_fingerprint == validation_digest
    messages.append(
        f'catalog-sha256-v1 {catalog_digest} '
        f'{"matches" if catalog_match else "does not match"} the stored catalog fingerprint.'
    )
    messages.append(
        f'validation-sha256-v1 {validation_digest} '
        f'{"matches" if validation_match else "does not match"} the stored validation fingerprint.'
    )
    for field in placeholders:
        messages.append(
            f'{field} is an unverified placeholder. The evaluator did not replace it.'
        )
    return PublicationEvaluation(
        cap_blockers=cap_blockers,
        fingerprint_scheme=scheme,
        catalog_digest=catalog_digest,
        validation_digest=validation_digest,
        catalog_digest_match=catalog_match,
        validation_digest_match=validation_match,
        fingerprint_verification=(
            'match' if catalog_match and validation_match else 'mismatch'
        ),
        unverified_placeholder_fields=placeholders,
        meets_publication_requirements=(
            not cap_blockers and catalog_match and validation_match
        ),
        messages=tuple(messages),
    )
