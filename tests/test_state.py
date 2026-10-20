import multiprocessing
import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import yaml

from rh_support_lib.services.state import StateService


def _paused_update(path, loaded, release, operation):
    service = StateService(state_file=path)
    original = service.load_state

    def load():
        data = original()
        loaded.set()
        if not release.wait(10):
            raise RuntimeError("Timed out waiting to finish transaction")
        return data

    service.load_state = load
    if operation == "read":
        service.mark_case_read("user", "123", last_comment_id="read-id")
    else:
        service.set_tags("user", "first", ["first"])


def _process_update(path, done, operation, started=None):
    service = StateService(state_file=path)
    if started is not None:
        started.set()
    if operation == "latest":
        service.batch_update_latest_known(
            "user", [{"case_number": "123", "latest_comment_id": "new-id"}]
        )
    else:
        service.set_tags("user", "second", ["second"])
    done.set()


def _write_many(path, worker, start):
    service = StateService(state_file=path)
    if not start.wait(10):
        raise RuntimeError("Timed out waiting for writers")
    for index in range(12):
        service.set_tags("user", f"{worker}-{index}", ["x" * 256])


class StateTransactionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = os.path.join(self.directory.name, "state.yaml")
        self.service = StateService(state_file=self.path)
        self.service.save_state({"version": 1, "users": {}})

    def _thread_race(self, first, second, operation, concurrent_operation):
        loaded = threading.Event()
        release = threading.Event()
        done = threading.Event()
        original = first.load_state

        def load():
            data = original()
            if loaded.is_set():
                return data
            loaded.set()
            if not release.wait(10):
                raise RuntimeError("Timed out waiting to finish transaction")
            return data

        def update():
            concurrent_operation(second)
            done.set()

        with (
            patch.object(first, "load_state", side_effect=load),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            pending = pool.submit(operation, first)
            try:
                self.assertTrue(loaded.wait(5))
                concurrent = pool.submit(update)
                # The old implementation completes the second write here,
                # then overwrites it with the paused writer's stale snapshot.
                done.wait(0.3)
            finally:
                release.set()
            pending.result(timeout=5)
            concurrent.result(timeout=5)

    def test_same_instance_threads_preserve_disjoint_updates(self):
        self._thread_race(
            self.service,
            self.service,
            lambda s: s.set_tags("user", "first", ["first"]),
            lambda s: s.set_tags("user", "second", ["second"]),
        )
        self.assertEqual(self.service.get_tags("user", "first"), ["first"])
        self.assertEqual(self.service.get_tags("user", "second"), ["second"])

    def test_multiple_instances_threads_preserve_disjoint_updates(self):
        self._thread_race(
            self.service,
            StateService(state_file=self.path),
            lambda s: s.set_tags("user", "first", ["first"]),
            lambda s: s.update_sync_info("user", "account", "now", 7),
        )
        self.assertEqual(self.service.get_tags("user", "first"), ["first"])
        self.assertEqual(
            self.service.get_sync_info("user", "account")["last_scanned_case_count"], 7
        )

    def test_toggle_check_is_in_transaction(self):
        self._thread_race(
            self.service,
            StateService(state_file=self.path),
            lambda s: s.toggle_follow("user", "123"),
            lambda s: s.toggle_follow("user", "123"),
        )
        self.assertFalse(self.service.is_following("123", "user"))

    def _process_race(self, operation, concurrent_operation):
        context = multiprocessing.get_context("spawn")
        loaded, release, done, started = (context.Event() for _ in range(4))
        first = context.Process(
            target=_paused_update, args=(self.path, loaded, release, operation)
        )
        second = context.Process(
            target=_process_update,
            args=(self.path, done, concurrent_operation, started),
        )
        first.start()
        try:
            self.assertTrue(loaded.wait(10))
            second.start()
            self.assertTrue(started.wait(10))
            done.wait(0.5)
        finally:
            release.set()
            for process in (first, second):
                if process.pid is not None:
                    process.join(10)
                    if process.is_alive():
                        process.terminate()
                        process.join(5)
        self.assertEqual(first.exitcode, 0)
        self.assertEqual(second.exitcode, 0)

    def test_processes_preserve_disjoint_updates(self):
        self._process_race("tags", "tags")
        self.assertEqual(self.service.get_tags("user", "first"), ["first"])
        self.assertEqual(self.service.get_tags("user", "second"), ["second"])

    def test_process_read_checkpoint_race_preserves_latest_known(self):
        self.service.add_followed_case("123", "user")
        self._process_race("read", "latest")
        case = self.service.get_tracked_cases("user")["123"]
        self.assertEqual(case.read_checkpoint.last_read_comment_id, "read-id")
        self.assertEqual(case.latest_known.latest_comment_id, "new-id")

    def test_concurrent_process_writes_never_publish_torn_yaml(self):
        context = multiprocessing.get_context("spawn")
        start = context.Event()
        processes = [
            context.Process(target=_write_many, args=(self.path, worker, start))
            for worker in range(3)
        ]
        for process in processes:
            process.start()
        start.set()
        reads = 0
        deadline = time.monotonic() + 15
        try:
            while any(process.is_alive() for process in processes):
                self.assertLess(time.monotonic(), deadline, "Writers deadlocked")
                with open(self.path) as stream:
                    data = yaml.safe_load(stream)
                self.assertEqual(data["version"], 1)
                self.assertIsInstance(data["users"], dict)
                reads += 1
        finally:
            for process in processes:
                process.join(10)
                if process.is_alive():
                    process.terminate()
                    process.join(5)
        self.assertGreater(reads, 0)
        for process in processes:
            self.assertEqual(process.exitcode, 0)
        self.assertEqual(len(self.service.load_state()["users"]["user"]["tags"]), 36)

    def test_exception_releases_file_lock_for_another_process(self):
        with (
            patch(
                "rh_support_lib.services.state.os.replace",
                side_effect=OSError("failure"),
            ),
            self.assertRaises(OSError),
        ):
            self.service.set_tags("user", "failed", ["failed"])
        context = multiprocessing.get_context("spawn")
        done = context.Event()
        process = context.Process(
            target=_process_update, args=(self.path, done, "tags")
        )
        process.start()
        try:
            self.assertTrue(done.wait(10), "File lock leaked after write failure")
        finally:
            process.join(5)
            if process.is_alive():
                process.terminate()
                process.join(5)
        self.assertEqual(process.exitcode, 0)
        self.assertEqual(self.service.get_tags("user", "failed"), [])
        self.assertEqual(self.service.get_tags("user", "second"), ["second"])

    def test_nested_toggle_and_batch_load_save_do_not_deadlock(self):
        errors = []

        def update():
            try:
                self.service.toggle_follow("user", "123")
                self.service.batch_update_latest_known(
                    "user", [{"case_number": "123", "status": "Waiting on Red Hat"}]
                )
            except (OSError, ValueError, TypeError, RuntimeError, KeyError) as error:
                errors.append(error)

        thread = threading.Thread(target=update, daemon=True)
        thread.start()
        thread.join(5)
        self.assertFalse(thread.is_alive(), "Nested transaction deadlocked")
        self.assertEqual(errors, [])
        case = self.service.get_tracked_cases("user")["123"]
        self.assertEqual(case.latest_known.status, "Waiting on Red Hat")

    def test_all_mutations_hold_lock_until_save(self):
        operations = {
            "add": lambda s: s.add_tracked_case("user", "123"),
            "add_batch": lambda s: s.add_tracked_cases_batch(
                "user", [{"case_number": "123"}]
            ),
            "remove": lambda s: s.remove_followed_case("123", "user"),
            "remove_batch": lambda s: s.remove_followed_cases_batch(["123"], "user"),
            "batch": lambda s: s.batch_update_latest_known(
                "user", [{"case_number": "123", "status": "Closed"}]
            ),
            "read": lambda s: s.mark_case_read("user", "123"),
            "sync": lambda s: s.update_sync_info("user", "account", "now", 3),
            "pinned": lambda s: s.set_pinned_cases("user", ["123"]),
            "recent": lambda s: s.add_recent_case("user", "123"),
            "tags": lambda s: s.set_tags("user", "123", ["tag"]),
            "layout": lambda s: s.set_tui_layout("user", "vertical"),
        }
        for name, operation in operations.items():
            with self.subTest(operation=name):
                self.service.save_state({"version": 1, "users": {}})
                self.service.add_followed_case("123", "user")
                self._thread_race(
                    self.service,
                    StateService(state_file=self.path),
                    operation,
                    lambda s: s.set_tags("other-user", "second", ["second"]),
                )
                self.assertEqual(
                    self.service.get_tags("other-user", "second"), ["second"]
                )

    def test_lock_errors_propagate_without_reading_or_writing_state(self):
        with patch(
            "rh_support_lib.services.state.fcntl.flock",
            side_effect=OSError("lock failed"),
        ):
            for operation in (
                lambda: self.service.save_state({"users": {"changed": {}}}),
                lambda: self.service.set_tags("user", "123", ["changed"]),
            ):
                with self.assertRaisesRegex(OSError, "lock failed"):
                    operation()
        self.assertEqual(self.service.load_state(), {"version": 1, "users": {}})

    def test_standalone_reads_do_not_require_writable_lock_directories(self):
        missing = os.path.join(self.directory.name, "missing", "state.yaml")
        reader = StateService(state_file=missing)
        with (
            patch(
                "rh_support_lib.services.state.os.makedirs",
                side_effect=PermissionError("read-only directory"),
            ),
            patch(
                "rh_support_lib.services.state.fcntl.flock",
                side_effect=AssertionError("read tried to lock"),
            ),
        ):
            self.assertEqual(self.service.load_state(), {"version": 1, "users": {}})
            self.assertEqual(reader.load_state(), {"version": 1, "users": {}})
        self.assertFalse(os.path.exists(os.path.dirname(missing)))

    def test_sidecar_inode_survives_state_replacement(self):
        lock_inode = os.stat(self.path + ".lock").st_ino
        self.service.set_tags("user", "123", ["tag"])
        self.assertEqual(os.stat(self.path + ".lock").st_ino, lock_inode)

    def test_explicit_and_custom_config_paths(self):
        with patch.dict(os.environ, {"RH_SUPPORT_STATE": "/unused/state.yaml"}):
            self.assertEqual(StateService(state_file=self.path).state_file, self.path)
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                StateService._resolve_state_path(
                    config_file=os.path.join(
                        self.directory.name, "custom", "config.yaml"
                    )
                ),
                os.path.join(self.directory.name, "custom", "state.yaml"),
            )

    def test_legacy_migration_uses_isolated_paths(self):
        legacy_dir = os.path.join(self.directory.name, "config", "rh-support-cli")
        os.makedirs(legacy_dir)
        legacy = os.path.join(legacy_dir, "state.yaml")
        StateService(state_file=legacy).save_state(
            {"version": 1, "users": {"user": {"following": ["123"]}}}
        )
        target = os.path.join(
            self.directory.name, "state", "rh-support-cli", "state.yaml"
        )
        with patch.dict(
            os.environ,
            {
                "XDG_STATE_HOME": os.path.join(self.directory.name, "state"),
                "XDG_CONFIG_HOME": os.path.join(self.directory.name, "config"),
            },
            clear=True,
        ):
            self.assertEqual(StateService._resolve_state_path(), target)
        self.assertFalse(os.path.exists(legacy))
        migrated = StateService(state_file=target)
        self.assertTrue(migrated.is_following("123", "user"))
        self.assertEqual(migrated.load_state()["users"]["user"]["following"], ["123"])

    def test_failed_migration_lock_retains_readable_legacy_state(self):
        config = os.path.join(self.directory.name, "legacy-config")
        state = os.path.join(self.directory.name, "new-state")
        legacy = os.path.join(config, "rh-support-cli", "state.yaml")
        StateService(state_file=legacy).save_state(
            {"version": 1, "users": {"legacy": {}}}
        )
        with (
            patch.dict(
                os.environ,
                {"XDG_CONFIG_HOME": config, "XDG_STATE_HOME": state},
                clear=True,
            ),
            patch(
                "rh_support_lib.services.state.fcntl.flock",
                side_effect=OSError("lock unavailable"),
            ),
        ):
            reader = StateService()
            self.assertEqual(reader.state_file, legacy)
            self.assertIn("legacy", reader.load_state()["users"])
        self.assertTrue(os.path.exists(legacy))
        self.assertFalse(
            os.path.exists(os.path.join(state, "rh-support-cli", "state.yaml"))
        )

    def test_batch_add_and_remove_tracked_cases(self):
        cases_data = [
            {
                "case_number": "1001",
                "summary": "First case",
                "account_number": "99999",
                "status": "In Progress",
                "severity": "High",
            },
            {
                "case_number": "1002",
                "summary": "Second case",
                "account_number": "99999",
                "status": "Waiting on Customer",
                "severity": "Normal",
            },
        ]
        # Pre-register 1001 as owned to test 'both' relationship
        self.service.register_owned_case("user", "1001")

        # Batch add
        added = self.service.add_tracked_cases_batch("user", cases_data)
        self.assertEqual(added, ["1001", "1002"])

        tracked = self.service.get_tracked_cases("user")
        self.assertEqual(tracked["1001"].relationship, "both")
        self.assertEqual(tracked["1001"].account_number, "99999")
        self.assertEqual(tracked["1002"].relationship, "followed")
        self.assertEqual(tracked["1002"].account_number, "99999")

        # Batch remove
        removed = self.service.remove_followed_cases_batch(["1001", "1002"], "user")
        self.assertEqual(removed, ["1001", "1002"])

        tracked_after = self.service.get_tracked_cases("user")
        # 1001 was 'both' so it should now be retained as 'owned'
        self.assertIn("1001", tracked_after)
        self.assertEqual(tracked_after["1001"].relationship, "owned")
        # 1002 was only 'followed' so it should be completely removed
        self.assertNotIn("1002", tracked_after)
        self.assertFalse(self.service.is_following("1002", "user"))
