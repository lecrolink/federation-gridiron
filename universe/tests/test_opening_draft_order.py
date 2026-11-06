import socket

from django.test import SimpleTestCase

from universe.generation.opening_draft_order import (
    FOUNDING_SLUGS,
    OPENING_DRAFT_ROUNDS,
    opening_draft_board,
    opening_draft_digest,
    opening_draft_message,
    opening_draft_order,
)


# Frozen from the approved proposal. This is the seed-1970, fg-1970-1 vector.
APPROVED_ORDER = (
    ('founding-25', '071073dcbf5ec33a54a9b6acab315dda20b7df14807fb3703f3f5e742fba7ae3'),
    ('founding-19', '16a6c10c0b67c7e65b755769457a20bf0a2ae344d5660cc215bfb130fbb0e999'),
    ('founding-17', '186fafcf3ed93c997c101a9a5cb7f8ca696550beb63b86368bd836109b0eebdc'),
    ('founding-14', '1aa7d01d8793b9fa1052e666200da4d0daba3a69976b1f0849bc750e6d13327e'),
    ('founding-05', '1c67129408fcbc88583c4b7895daadf863bda4b1aba898aff99e40d7ede1bc2a'),
    ('founding-13', '2b34d5c3d7567eb3d8c7453d1ca0e0a193b0a7c232cbeac868757bcc57707057'),
    ('founding-15', '4e704a4368963976e63b70432a5967c35150c4150a04243d18f286772f199630'),
    ('founding-08', '60df09ebd77ac18dff25f8fd09e3df9e80ce77427f0994d6328605da0eac9d04'),
    ('founding-26', '64a6d345e53f986871943313e723d278ddb932ca411f56bd28d37da55c3e5153'),
    ('founding-21', '6dcbc6cc723b6544e302635e4afef231a175888cbd631c37e5a5903e739e64dd'),
    ('founding-12', '70ae217eff320c80686f539a33940097ef76acde3bcd225563fc53153e98b932'),
    ('founding-03', '759b7e38018c0f3369ab0644300eaa70b2b4eaa0bf5eb94fc1de5897d09e7b5e'),
    ('founding-20', '7788700fafb507c74bc1e56d571c36efdd5090dd3fa6dc1e64dea453cfa6d87b'),
    ('founding-07', '7b2db3c64f6d3d93f24a7d28c51cbe9e08f3a352491aaa4831ad8e38446acfb2'),
    ('founding-02', '7d829776852aaefec492e5da92e5f31af8530447ded3c7f4b78295c7b0a244fe'),
    ('founding-09', '8a789cb843b73cc3260b4646aa80042b652e050bc58d4cc4345c1ae43c4f98d6'),
    ('founding-18', '8c05cb90c77c62a1382c2fba1f4188491a3c8c94d4508bb1b79224604ee0fab2'),
    ('founding-23', '9874b214ca62aadb638051e14e9590a5a88c151c62f56d0d31880e3a2719c4ef'),
    ('founding-24', '9a3c88435cf6b327c61e31154e6dc3ad7cc29d381cee3e122541440306170690'),
    ('founding-06', '9dbd7a1c65ca34e3c24854f267ad7312d486e2e39e5ea73ce46c29b93a3c866d'),
    ('founding-04', 'a34a6568165d06e41a1fc35ce931d39572a087298c362a527c670d42064507c2'),
    ('founding-01', 'c1a44ed69b6a82931eaf337b80e5e21a478f31ed54a647255a058a43316b4ed4'),
    ('founding-16', 'c375c85eef0b95b4a3845d838c31b4f2d2d95d9b290f2d05660b50dbea9cd0ce'),
    ('founding-11', 'c6e537cdebd50ece657606443e1ae93be507d380cc8e8cca835a61225f268b23'),
    ('founding-22', 'ed0935b7d0f16267dd159542bbfd42981cdd2e8492ea498040b44290d56a2691'),
    ('founding-10', 'ff0931d98f6295b94c4f2169db22ef9a39fa0aaf22e8c692e8e0706d6003553e'),
)

_ORIGINAL_CONNECT = socket.socket.connect


def _blocked_connect(self, address):
    raise AssertionError(f'Outbound connections are blocked: {address}')


def setUpModule():
    socket.socket.connect = _blocked_connect


def tearDownModule():
    socket.socket.connect = _ORIGINAL_CONNECT


class OpeningDraftOrderTests(SimpleTestCase):
    def test_seed_1970_matches_the_approved_vector(self):
        order = opening_draft_order(1970, 'fg-1970-1')
        self.assertEqual(len(order), 26)
        self.assertEqual(OPENING_DRAFT_ROUNDS, 17)
        found = tuple((pick.franchise_slug, pick.digest_hex) for pick in order)
        self.assertEqual(found, APPROVED_ORDER)
        digests = [pick.digest_hex for pick in order]
        self.assertEqual(len(set(digests)), 26)
        for pick, (slug, digest_hex) in zip(order, APPROVED_ORDER):
            self.assertEqual(pick.pick, order.index(pick) + 1)
            self.assertEqual(len(digest_hex), 64)
            self.assertEqual(
                opening_draft_message('fg-1970-1', slug),
                f'opening-draft-order:fg-1970-1:{slug}',
            )
            self.assertEqual(
                opening_draft_digest(1970, 'fg-1970-1', slug).hex(),
                digest_hex,
            )
            self.assertEqual(len(opening_draft_digest(1970, 'fg-1970-1', slug)), 32)
        board = opening_draft_board(1970, 'fg-1970-1')
        self.assertEqual(len(board), 17)
        self.assertTrue(all(round_order == order for round_order in board))

    def test_another_seed_or_slug_reshuffles_the_same_26_clubs(self):
        base = tuple(
            pick.franchise_slug for pick in opening_draft_order(1970, 'fg-1970-1')
        )
        other_seed = tuple(
            pick.franchise_slug for pick in opening_draft_order(1971, 'fg-1970-1')
        )
        other_slug = tuple(
            pick.franchise_slug for pick in opening_draft_order(1970, 'fg-1970-2')
        )
        self.assertNotEqual(other_seed, base)
        self.assertNotEqual(other_slug, base)
        self.assertEqual(len(other_seed), 26)
        self.assertEqual(len(set(other_seed)), 26)
        self.assertEqual(set(other_seed), set(FOUNDING_SLUGS))
        self.assertEqual(set(other_slug), set(FOUNDING_SLUGS))
        self.assertEqual(len(opening_draft_board(1971, 'fg-1970-1')), 17)
