"""Read-only check of whether a draft release meets publication rules.

This module does not publish a release, write a cap amount, or certify a
fingerprint. No fingerprint algorithm has been approved, so every stored
fingerprint stays unresolved. The known smoke-test strings are reported as
placeholders and are not replaced.
"""

from typing import NamedTuple

from universe.rules import CAP_STATUS


# The stored EraRuleProfile value. Cap-off profiles keep a null cap_limit.
CAP_ON = 'on'
if CAP_ON not in CAP_STATUS:
    raise RuntimeError('The stored cap-on status is missing.')

# Exact strings used by the disposable smoke test. Matching them is not a
# hash check and does not accept any other spelling.
SMOKE_FINGERPRINT_PLACEHOLDERS = frozenset((
    'smoke-catalog-fingerprint',
    'smoke-validation-fingerprint',
))
FINGERPRINT_FIELDS = ('catalog_fingerprint', 'validation_fingerprint')
FINGERPRINT_VERIFICATION_UNRESOLVED = 'unresolved'


class PublicationEvaluation(NamedTuple):
    """A report. It is not a saved publication."""

    cap_blockers: tuple
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
    messages.append(
        'Fingerprint verification is unresolved. No fingerprint algorithm '
        'has been approved, so the stored fingerprints cannot be certified '
        'as content hashes.'
    )
    for field in placeholders:
        messages.append(
            f'{field} is an unverified placeholder. The evaluator did not replace it.'
        )
    return PublicationEvaluation(
        cap_blockers=cap_blockers,
        fingerprint_verification=FINGERPRINT_VERIFICATION_UNRESOLVED,
        unverified_placeholder_fields=placeholders,
        meets_publication_requirements=False,
        messages=tuple(messages),
    )
