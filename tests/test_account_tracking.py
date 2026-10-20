import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import Mock, patch

from rh_support_lib.api import RedHatAPIClient
from rh_support_lib.commands.watchers import cmd_follow, cmd_unfollow
from rh_support_lib.services.cache import CacheService
from rh_support_lib.services.cases import CaseService
from rh_support_lib.services.state import StateService


class AccountTrackingTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        env = patch.dict(os.environ, {"RH_NO_CACHE": "0"})
        env.start()
        self.addCleanup(env.stop)
        self.client = Mock(spec=RedHatAPIClient)
        self.client.get_cache_scope.return_value = "account-test"
        self.state = StateService(state_file=os.path.join(directory.name, "state.yaml"))
        self.cache = CacheService(cache_dir=directory.name, scope="account-test")
        self.service = CaseService(
            self.client, state_service=self.state, cache_service=self.cache
        )
        self.service.get_current_user = Mock(return_value="user")

    def test_per_case_dry_run_preserves_tracking_state(self):
        self.state.add_tracked_case("user", "existing")
        before = self.state.load_state()
        for command, number in ((cmd_follow, "new"), (cmd_unfollow, "existing")):
            with (
                self.subTest(command=command.__name__),
                patch(
                    "rh_support_lib.commands.watchers.CaseService",
                    return_value=self.service,
                ),
                patch.object(self.service, "get_case_details", return_value=None),
                redirect_stdout(io.StringIO()) as output,
            ):
                self.state.save_state(before)
                command(
                    SimpleNamespace(
                        cases=[number], case=[number], user="user", dry_run=True
                    ),
                    self.client,
                )
                self.assertEqual(self.state.load_state(), before)
                self.assertIn("[Dry-run]", output.getvalue())
                self.assertIn(number, output.getvalue())
        self.client.execute_graphql.assert_not_called()
