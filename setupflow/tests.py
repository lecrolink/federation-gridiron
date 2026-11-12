import re
from unittest.mock import patch

from django.db import connections
from django.test import Client, RequestFactory, SimpleTestCase
from django.urls import reverse

from setupflow.flow import (
    CHOICES,
    CONFERENCES,
    COOKIE_NAME,
    FRANCHISES,
    LABELS,
    read_state,
)
from timelines.models import (
    DEFENSIVE_EMPHASIS,
    FOURTH_DOWN,
    OFFENSIVE_EMPHASIS,
    PLAYING_TIME,
    TEAM_DIRECTIONS,
    TRADE_APPETITES,
    VETERAN_PREFERENCES,
)
from universe.people import GM_PRIORITIES


EXPECTED_ALIGNMENT = {
    'founding-01': ('conference-1', 'c1-d1', 1),
    'founding-02': ('conference-1', 'c1-d1', 2),
    'founding-03': ('conference-1', 'c1-d1', 3),
    'founding-04': ('conference-1', 'c1-d1', 4),
    'founding-05': ('conference-1', 'c1-d2', 1),
    'founding-06': ('conference-1', 'c1-d2', 2),
    'founding-07': ('conference-1', 'c1-d2', 3),
    'founding-08': ('conference-1', 'c1-d2', 4),
    'founding-09': ('conference-1', 'c1-d3', 1),
    'founding-10': ('conference-1', 'c1-d3', 2),
    'founding-11': ('conference-1', 'c1-d3', 3),
    'founding-12': ('conference-1', 'c1-d3', 4),
    'founding-13': ('conference-2', 'c2-d1', 1),
    'founding-14': ('conference-2', 'c2-d1', 2),
    'founding-15': ('conference-2', 'c2-d1', 3),
    'founding-16': ('conference-2', 'c2-d1', 4),
    'founding-17': ('conference-2', 'c2-d2', 1),
    'founding-18': ('conference-2', 'c2-d2', 2),
    'founding-19': ('conference-2', 'c2-d2', 3),
    'founding-20': ('conference-2', 'c2-d2', 4),
    'founding-21': ('conference-2', 'c2-d3', 1),
    'founding-22': ('conference-2', 'c2-d3', 2),
    'founding-23': ('conference-2', 'c2-d3', 3),
    'founding-24': ('conference-2', 'c2-d3', 4),
}


class SetupPrototypeTests(SimpleTestCase):
    def setUp(self):
        self.client = Client()

    def guarded(self, func):
        connection = connections['default']

        def blocked(*args, **kwargs):
            raise AssertionError('The setup prototype opened a database connection.')

        with patch.object(connection, 'cursor', blocked), \
                patch.object(connection, 'connect', blocked), \
                patch.object(connection, 'ensure_connection', blocked):
            return func()

    def test_placeholder_catalog_matches_the_1970_alignment(self):
        self.assertEqual(set(FRANCHISES), set(EXPECTED_ALIGNMENT))
        for slug, (conference, division, order) in EXPECTED_ALIGNMENT.items():
            club = FRANCHISES[slug]
            self.assertEqual(club['conference'], conference)
            self.assertEqual(club['division'], division)
            self.assertEqual(club['slot_order'], order)
            self.assertEqual(club['first_season'], 1970)
            self.assertEqual(len(club['mates']), 3)
        self.assertEqual(
            [conference['slug'] for conference in CONFERENCES],
            ['conference-1', 'conference-2'],
        )

    def test_labels_use_the_model_choices(self):
        expected = {
            'team_direction': TEAM_DIRECTIONS,
            'trade_appetite': TRADE_APPETITES,
            'veteran_preference': VETERAN_PREFERENCES,
            'priority': GM_PRIORITIES,
            'offensive_emphasis': OFFENSIVE_EMPHASIS,
            'defensive_emphasis': DEFENSIVE_EMPHASIS,
            'fourth_down': FOURTH_DOWN,
            'playing_time': PLAYING_TIME,
        }
        self.assertEqual(CHOICES, expected)
        for group, values in expected.items():
            self.assertEqual(set(LABELS[group]), set(values))

    def test_landing_and_franchise_list(self):
        landing = self.guarded(lambda: self.client.get('/'))
        self.assertContains(landing, 'Take a founding franchise')
        self.assertContains(landing, '24 clubs')
        listing = self.guarded(lambda: self.client.get('/franchises/'))
        self.assertContains(listing, 'Conference 1')
        self.assertContains(listing, 'Conference 2')
        for slug in EXPECTED_ALIGNMENT:
            self.assertContains(listing, slug)

    def test_overview_shows_the_placeholder_club(self):
        response = self.guarded(lambda: self.client.get('/franchises/founding-07/'))
        self.assertContains(response, 'Founding franchise 07')
        self.assertContains(response, 'c1-d2')
        self.assertContains(response, 'Opening general manager')
        self.assertContains(response, 'Take this club')
        self.assertContains(response, 'csrfmiddlewaretoken')

    def test_unknown_franchise_stays_on_the_prototype_page(self):
        response = self.guarded(lambda: self.client.get('/franchises/founding-99/'))
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, 'not in the 1970 list', status_code=404)

    def test_staff_requires_a_taken_club(self):
        response = self.guarded(
            lambda: self.client.get('/franchises/founding-07/staff/', follow=False)
        )
        self.assertRedirects(
            response,
            '/franchises/founding-07/',
            fetch_redirect_response=False,
        )
        self.assertNotIn(COOKIE_NAME, response.cookies)

    def test_keep_or_replace_and_instructions_round_trip(self):
        def run():
            taken = self.client.post('/franchises/founding-04/', follow=True)
            self.assertContains(taken, 'Opening general manager')
            self.assertContains(taken, 'value="keep" checked')
            replaced = self.client.post('/franchises/founding-04/staff/', {
                'gm_choice': 'replace',
                'gm_replacement': 'available-gm-01',
                'hc_choice': 'keep',
            }, follow=True)
            self.assertContains(replaced, 'Instructions for the general manager')
            self.assertContains(replaced, 'value="balanced" checked')
            instructed = self.client.post('/franchises/founding-04/gm/', {
                'team_direction': 'rebuild',
                'priority_group_1': 'quarterback',
                'priority_group_2': 'offensive_line',
                'priority_group_3': '',
                'trade_appetite': 'low',
                'veteran_preference': 'youth',
            }, follow=True)
            self.assertContains(instructed, 'Instructions for the head coach')
            finished = self.client.post('/franchises/founding-04/coach/', {
                'offensive_emphasis': 'run',
                'defensive_emphasis': 'stop_run',
                'fourth_down': 'conservative',
                'playing_time': 'develop_youth',
            }, follow=True)
            return finished

        review = self.guarded(run)
        self.assertContains(review, 'Founding franchise 04')
        self.assertContains(review, 'Available general manager 01')
        self.assertContains(review, 'Keep the opening head coach')
        self.assertContains(review, 'Rebuild')
        self.assertContains(review, 'Quarterback, Offensive line')
        self.assertContains(review, 'Stop the run')
        self.assertContains(review, 'cookie')
        cookie = self.client.cookies[COOKIE_NAME]
        self.assertTrue(cookie['httponly'])
        request = RequestFactory().get('/')
        request.COOKIES[COOKIE_NAME] = cookie.value
        state = read_state(request)
        self.assertEqual(state['franchise'], 'founding-04')
        self.assertEqual(state['gm_replacement'], 'available-gm-01')
        self.assertEqual(state['team_direction'], 'rebuild')
        self.assertEqual(state['fourth_down'], 'conservative')

    def test_replace_requires_a_placeholder_candidate(self):
        self.guarded(lambda: self.client.post('/franchises/founding-01/', follow=True))

        def post():
            return self.client.post('/franchises/founding-01/staff/', {
                'gm_choice': 'replace',
                'hc_choice': 'replace',
                'hc_replacement': 'available-hc-03',
            })

        response = self.guarded(post)
        self.assertContains(response, 'Choose the replacement general manager.')
        request = RequestFactory().get('/')
        request.COOKIES[COOKIE_NAME] = self.client.cookies[COOKIE_NAME].value
        state = read_state(request)
        self.assertEqual(state['gm_choice'], 'keep')
        self.assertEqual(state['hc_choice'], 'keep')

    def test_priority_groups_stay_ordered_and_distinct(self):
        self.guarded(lambda: self.client.post('/franchises/founding-12/', follow=True))
        self.guarded(lambda: self.client.post('/franchises/founding-12/staff/', {
            'gm_choice': 'keep',
            'hc_choice': 'keep',
        }, follow=True))

        def duplicate():
            return self.client.post('/franchises/founding-12/gm/', {
                'team_direction': 'compete',
                'priority_group_1': 'skill',
                'priority_group_2': 'skill',
                'priority_group_3': '',
                'trade_appetite': 'high',
                'veteran_preference': 'automatic',
            })

        self.assertContains(
            self.guarded(duplicate),
            'Name each position group only once.',
        )

        def gap():
            return self.client.post('/franchises/founding-12/gm/', {
                'team_direction': 'compete',
                'priority_group_1': '',
                'priority_group_2': 'linebacker',
                'priority_group_3': '',
                'trade_appetite': 'high',
                'veteran_preference': 'automatic',
            })

        self.assertContains(
            self.guarded(gap),
            'Set the first priority before the second.',
        )

    def test_taking_another_club_clears_the_earlier_staff_choice(self):
        def run():
            self.client.post('/franchises/founding-02/', follow=True)
            self.client.post('/franchises/founding-02/staff/', {
                'gm_choice': 'replace',
                'gm_replacement': 'available-gm-04',
                'hc_choice': 'keep',
            }, follow=True)
            return self.client.post('/franchises/founding-18/', follow=True)

        staff = self.guarded(run)
        self.assertContains(staff, 'value="keep" checked')
        self.assertNotContains(staff, 'value="available-gm-04" checked')
        bounced = self.guarded(
            lambda: self.client.get('/franchises/founding-02/staff/', follow=False)
        )
        self.assertRedirects(
            bounced,
            '/franchises/founding-02/',
            fetch_redirect_response=False,
        )

    def test_same_club_keeps_the_staff_choice(self):
        def run():
            self.client.post('/franchises/founding-02/', follow=True)
            self.client.post('/franchises/founding-02/staff/', {
                'gm_choice': 'replace',
                'gm_replacement': 'available-gm-04',
                'hc_choice': 'keep',
            }, follow=True)
            return self.client.post('/franchises/founding-02/', follow=True)

        staff = self.guarded(run)
        self.assertContains(staff, 'value="replace" checked')
        self.assertContains(staff, 'value="available-gm-04" checked')

    def test_start_over_clears_the_cookie(self):
        self.guarded(lambda: self.client.post('/franchises/founding-09/', follow=True))
        self.assertIn(COOKIE_NAME, self.client.cookies)

        def reset():
            return self.client.post(reverse('setupflow:reset'), follow=True)

        response = self.guarded(reset)
        self.assertContains(response, 'Take a founding franchise')
        self.assertEqual(self.client.cookies[COOKIE_NAME].value, '')
        request = RequestFactory().get('/')
        request.COOKIES[COOKIE_NAME] = ''
        self.assertEqual(read_state(request)['franchise'], '')

    def test_browser_post_accepts_the_page_csrf_token(self):
        client = Client(enforce_csrf_checks=True)

        def run():
            page = client.get('/franchises/founding-03/')
            match = re.search(
                r'name="csrfmiddlewaretoken" value="([^"]+)"',
                page.content.decode(),
            )
            self.assertIsNotNone(match)
            return client.post('/franchises/founding-03/', {
                'csrfmiddlewaretoken': match.group(1),
            }, follow=True)

        staff = self.guarded(run)
        self.assertContains(staff, 'General manager and head coach')
