"""Locks for N(year), the documented draft-class table, and quota sums.

No database. The module under test does not create players.
"""

from django.test import SimpleTestCase

from universe.generation.player_counts import (
    EXPANSION_YEARS,
    FIRST_SEASON_YEAR,
    LAST_SEASON_YEAR,
    OPENING_ROSTER_INCLUDES_ROOKIE_CLASS,
    POSITIONS,
    SEASON_COUNT,
    SHARED_PEOPLE_CREATED_BY_EXPANSION_DRAFT,
    UNDRAFTED_MULTIPLIER,
    active_quota,
    draft_class,
    draft_classes,
    draft_rounds,
    n_year,
    opening_cohort_count,
    opening_free_agent_count,
    opening_rostered_count,
    playable_identity_count,
    prospect_totals,
    quota_total,
    taxi_quota,
)


# Years, seasons, N, rounds, draftable, undrafted, class size, prospects.
CLASS_GROUPS = (
    (1970, 1975, 6, 26, 17, 2652, 312, 494, 2964),
    (1976, 1977, 2, 28, 17, 952, 112, 532, 1064),
    (1978, 1986, 9, 28, 12, 3024, 504, 392, 3528),
    (1987, 1994, 8, 28, 8, 1792, 448, 280, 2240),
    (1995, 1995, 1, 30, 7, 210, 60, 270, 270),
    (1996, 1998, 3, 30, 7, 630, 180, 270, 810),
    (1999, 2001, 3, 31, 7, 651, 186, 279, 837),
    (2002, 2003, 2, 32, 7, 448, 128, 288, 576),
    (2004, 2013, 10, 32, 7, 2240, 640, 288, 2880),
    (2014, 2029, 16, 32, 7, 3584, 1024, 288, 4608),
)


class NYearTests(SimpleTestCase):
    def test_schedule_boundaries(self):
        self.assertEqual(n_year(1970), 26)
        self.assertEqual(n_year(1975), 26)
        self.assertEqual(n_year(1976), 28)
        self.assertEqual(n_year(1994), 28)
        self.assertEqual(n_year(1995), 30)
        self.assertEqual(n_year(1998), 30)
        self.assertEqual(n_year(1999), 31)
        self.assertEqual(n_year(2001), 31)
        self.assertEqual(n_year(2002), 32)
        self.assertEqual(n_year(2029), 32)

    def test_sixty_seasons_and_span_lengths(self):
        self.assertEqual(SEASON_COUNT, 60)
        counts = {}
        for year in range(FIRST_SEASON_YEAR, LAST_SEASON_YEAR + 1):
            counts[n_year(year)] = counts.get(n_year(year), 0) + 1
        self.assertEqual(counts, {26: 6, 28: 19, 30: 4, 31: 3, 32: 28})

    def test_years_outside_the_schedule_are_refused(self):
        for year in (1969, 2030, True, '1970'):
            with self.assertRaises(ValueError):
                n_year(year)

    def test_expansion_years_enlarge_n_year(self):
        self.assertEqual(EXPANSION_YEARS, (1976, 1995, 1999, 2002))
        self.assertEqual(
            tuple(n_year(year) - n_year(year - 1) for year in EXPANSION_YEARS),
            (2, 2, 1, 1),
        )
        self.assertEqual(SHARED_PEOPLE_CREATED_BY_EXPANSION_DRAFT, 0)


class DraftClassCountTests(SimpleTestCase):
    def test_documented_class_table(self):
        classes = draft_classes()
        self.assertEqual(len(classes), 60)
        self.assertEqual(
            [row.season_year for row in classes],
            list(range(1970, 2030)),
        )
        for (
            start, end, seasons, clubs, rounds, draftable, undrafted, class_size, prospects,
        ) in CLASS_GROUPS:
            rows = [row for row in classes if start <= row.season_year <= end]
            self.assertEqual(len(rows), seasons)
            self.assertTrue(all(row.franchise_count == clubs for row in rows))
            self.assertTrue(all(row.rounds == rounds for row in rows))
            self.assertTrue(all(row.class_size == class_size for row in rows))
            self.assertEqual(sum(row.draftable_count for row in rows), draftable)
            self.assertEqual(sum(row.undrafted_count for row in rows), undrafted)
            self.assertEqual(sum(row.class_size for row in rows), prospects)

    def test_each_class_uses_n_year_and_the_undrafted_multiplier(self):
        for year in range(FIRST_SEASON_YEAR, LAST_SEASON_YEAR + 1):
            row = draft_class(year)
            self.assertEqual(row.franchise_count, n_year(year))
            self.assertEqual(row.rounds, draft_rounds(year))
            self.assertEqual(row.draftable_count, row.rounds * n_year(year))
            self.assertEqual(row.undrafted_count, UNDRAFTED_MULTIPLIER * n_year(year))
            self.assertEqual(UNDRAFTED_MULTIPLIER, 2)

    def test_locked_identity_totals(self):
        draftable, undrafted, prospects = prospect_totals()
        self.assertEqual(draftable, 16183)
        self.assertEqual(undrafted, 3594)
        self.assertEqual(prospects, 19777)
        self.assertEqual(opening_rostered_count(), 1248)
        self.assertEqual(opening_free_agent_count(), 130)
        self.assertEqual(opening_cohort_count(), 1378)
        self.assertEqual(playable_identity_count(), 21155)
        self.assertEqual(
            playable_identity_count(),
            opening_cohort_count() + prospects,
        )

    def test_1970_class_stays_off_the_opening_roster(self):
        opening_year = draft_class(1970)
        self.assertEqual(opening_year.class_size, 494)
        self.assertEqual(opening_year.draftable_count, 442)
        self.assertEqual(opening_year.undrafted_count, 52)
        self.assertFalse(OPENING_ROSTER_INCLUDES_ROOKIE_CLASS)
        self.assertEqual(opening_cohort_count(), 1378)
        self.assertNotEqual(
            opening_cohort_count(),
            opening_cohort_count() + opening_year.class_size,
        )

    def test_expansion_classes_follow_that_years_n(self):
        self.assertEqual(SHARED_PEOPLE_CREATED_BY_EXPANSION_DRAFT, 0)
        for year in EXPANSION_YEARS:
            row = draft_class(year)
            self.assertEqual(row.franchise_count, n_year(year))
            self.assertGreater(row.franchise_count, n_year(year - 1))
            self.assertEqual(
                row.class_size,
                n_year(year) * (row.rounds + UNDRAFTED_MULTIPLIER),
            )


class QuotaSumTests(SimpleTestCase):
    def test_documented_quota_sums(self):
        self.assertEqual(len(POSITIONS), 19)
        for year in range(FIRST_SEASON_YEAR, LAST_SEASON_YEAR + 1):
            active = active_quota(year)
            taxi = taxi_quota(year)
            self.assertEqual(set(active), set(POSITIONS))
            self.assertEqual(set(taxi), set(POSITIONS))
            self.assertEqual(quota_total(taxi), 8)
            if year <= 1977:
                self.assertEqual(quota_total(active), 40)
            elif year <= 1986:
                self.assertEqual(quota_total(active), 45)
            elif year <= 1994:
                self.assertEqual(quota_total(active), 47)
            else:
                self.assertEqual(quota_total(active), 53)

    def test_quota_rows_are_the_documented_fills(self):
        self.assertEqual(active_quota(1970), {
            'QB': 2, 'HB': 2, 'FB': 2, 'WR': 4, 'TE': 2,
            'LT': 2, 'LG': 1, 'C': 2, 'RG': 1, 'RT': 2,
            'DE': 3, 'DT': 4, 'ILB': 2, 'OLB': 4, 'CB': 3,
            'FS': 1, 'SS': 1, 'K': 1, 'P': 1,
        })
        self.assertEqual(active_quota(1978), {
            'QB': 2, 'HB': 2, 'FB': 2, 'WR': 6, 'TE': 2,
            'LT': 2, 'LG': 2, 'C': 2, 'RG': 1, 'RT': 2,
            'DE': 3, 'DT': 4, 'ILB': 2, 'OLB': 4, 'CB': 4,
            'FS': 1, 'SS': 2, 'K': 1, 'P': 1,
        })
        self.assertEqual(active_quota(1987), {
            'QB': 2, 'HB': 2, 'FB': 1, 'WR': 7, 'TE': 2,
            'LT': 2, 'LG': 2, 'C': 2, 'RG': 2, 'RT': 2,
            'DE': 3, 'DT': 4, 'ILB': 2, 'OLB': 4, 'CB': 5,
            'FS': 1, 'SS': 2, 'K': 1, 'P': 1,
        })
        self.assertEqual(active_quota(1995), {
            'QB': 2, 'HB': 2, 'FB': 1, 'WR': 9, 'TE': 2,
            'LT': 2, 'LG': 2, 'C': 2, 'RG': 2, 'RT': 2,
            'DE': 4, 'DT': 4, 'ILB': 3, 'OLB': 4, 'CB': 6,
            'FS': 2, 'SS': 2, 'K': 1, 'P': 1,
        })
        self.assertEqual(taxi_quota(1970), {
            'QB': 1, 'HB': 1, 'FB': 0, 'WR': 1, 'TE': 0,
            'LT': 0, 'LG': 1, 'C': 0, 'RG': 1, 'RT': 0,
            'DE': 1, 'DT': 0, 'ILB': 1, 'OLB': 0, 'CB': 0,
            'FS': 1, 'SS': 0, 'K': 0, 'P': 0,
        })
        self.assertEqual(taxi_quota(2029), taxi_quota(1970))
        returned_taxi = taxi_quota(1970)
        returned_taxi['QB'] = 0
        self.assertEqual(taxi_quota(1970)['QB'], 1)
        returned_active = active_quota(1970)
        returned_active['QB'] = 0
        self.assertEqual(active_quota(1970)['QB'], 2)
