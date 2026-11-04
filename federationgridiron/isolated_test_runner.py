"""Run pure tests without opening a database connection."""

from django.test.runner import DiscoverRunner


class IsolatedTestRunner(DiscoverRunner):
    def setup_databases(self, **kwargs):
        return []

    def teardown_databases(self, old_config, **kwargs):
        return None
