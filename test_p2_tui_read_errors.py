"""Offline read-failure presentation through Textual's real UI dispatcher."""

import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from textual.widgets import DataTable, Static, TextArea

from rh_support_lib.api import RedHatAPIClient
from rh_support_lib.errors import IncompleteCaseHistoryError, ReadError
from rh_support_lib.models import Attachment, Case, CaseComment, CaseDetails
from rh_support_lib.tui.app import SupportApp


def details(number="1", version="old"):
    return CaseDetails(
        Case(
            number,
            "Case " + number,
            "Waiting on Red Hat",
            "3",
            last_modified_date=version,
        ),
        "Description " + version,
    )


class ReadPresentationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.service = Mock()
        self.service.get_current_user.return_value = "offline"
        self.service.state_service.get_tui_layout.return_value = "vertical"
        self.service.state_service.get_tracked_cases.return_value = {}
        self.service.cache_service.get_case_details.return_value = None
        self.attachments = Mock()
        self.attachments.list_attachments.return_value = []
        for patcher in (
            patch("rh_support_lib.tui.app.CaseService", return_value=self.service),
            patch(
                "rh_support_lib.tui.app.AttachmentService",
                return_value=self.attachments,
            ),
            patch.object(
                SupportApp,
                "on_mount",
                lambda app: app.query_one(DataTable).add_columns(
                    "NUMBER", "TITLE", "SEVERITY", "STATUS"
                ),
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.app = SupportApp(Mock(spec=RedHatAPIClient), {"tui": {"poll_interval": 0}})
        self.tasks = []
        self.releases = []
        self.warnings = []

        def notify(message, **kwargs):
            self.assertEqual(threading.current_thread(), threading.main_thread())
            self.warnings.append((message, kwargs))

        self.app.notify = Mock(side_effect=notify)

    async def asyncTearDown(self):
        for release in self.releases:
            release.set()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    def start(self, fn):
        task = asyncio.create_task(asyncio.to_thread(fn))
        self.tasks.append(task)
        return task

    def gate_error(self):
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)

        def fail(*args, **kwargs):
            self.assertNotEqual(threading.current_thread(), threading.main_thread())
            entered.set()
            if not release.wait(5):
                raise AssertionError("gate was not released")
            raise ReadError("offline")

        return fail, entered, release

    async def reached(self, event):
        self.assertTrue(await asyncio.to_thread(event.wait, 5))

    async def show_good_view(self, pilot):
        self.app.selected_case_id = "1"
        old = details()
        atts = [
            Mock(
                spec=Attachment,
                size_kb=1,
                file_name="old.txt",
                created_by="user",
                upload_status="Done",
            )
        ]
        self.app.cases = [old.case]
        self.app.populate_cases_table()
        self.app.render_case_details(old, atts)
        self.app.action_add_comment()
        await pilot.pause()
        self.app.query_one(TextArea).text = "keep this draft"
        return old, atts, tuple(self.app.query_one("#case-detail-container").children)

    async def test_list_failure_retains_list_and_widgets_but_valid_empty_clears(self):
        async with self.app.run_test() as pilot:
            old, atts, widgets = await self.show_good_view(pilot)
            cases = self.app.cases
            self.service.list_cases.side_effect = ReadError("offline")
            await self.start(self.app.fetch_cases)
            self.assertIs(self.app.cases, cases)
            self.assertEqual(self.app.query_one(DataTable).row_count, 1)
            self.assertIs(self.app.current_details, old)
            self.assertIs(self.app.current_attachments, atts)
            self.assertEqual(
                tuple(self.app.query_one("#case-detail-container").children), widgets
            )
            self.assertEqual(self.app.query_one(TextArea).text, "keep this draft")
            self.assertEqual(self.warnings[-1][1]["severity"], "warning")
            self.service.list_cases.side_effect = None
            self.service.list_cases.return_value = []
            await self.start(self.app.fetch_cases)
            self.assertEqual(self.app.cases, [])
            self.assertEqual(self.app.query_one(DataTable).row_count, 0)

    async def test_detail_and_attachment_failures_retain_complete_view_and_draft(self):
        async with self.app.run_test() as pilot:
            old, atts, widgets = await self.show_good_view(pilot)
            for failure in ("details", "attachments", "poll-attachments"):
                with self.subTest(failure=failure):
                    self.service.get_case_details.side_effect = (
                        IncompleteCaseHistoryError("incomplete")
                        if failure == "details"
                        else None
                    )
                    self.service.get_case_details.return_value = details(version="new")
                    self.attachments.list_attachments.side_effect = (
                        ReadError("attachments unavailable")
                        if failure != "details"
                        else None
                    )
                    self.service.list_cases.return_value = [details(version="new").case]
                    generation = self.app._read_generation
                    timer = Mock()
                    self.app._read_timer = timer
                    if failure.startswith("poll"):
                        await self.start(self.app.poll_cases_background)
                        self.assertFalse(self.app._poll_in_flight)
                    else:
                        await self.start(lambda: self.app.fetch_case_details("1", True))
                    self.assertIs(self.app.current_details, old)
                    self.assertIs(self.app.current_attachments, atts)
                    self.assertEqual(
                        tuple(self.app.query_one("#case-detail-container").children),
                        widgets,
                    )
                    self.assertEqual(
                        self.app.query_one(TextArea).text, "keep this draft"
                    )
                    self.app._auto_mark_read_dwell("1", generation, old)
                    self.service.state_service.mark_case_read.assert_not_called()
                    timer.stop.assert_called_once()
            self.assertEqual(len(self.warnings), 3)

    async def test_initial_errors_replace_loading_with_retry_placeholder(self):
        async with self.app.run_test() as pilot:
            self.service.list_cases.side_effect = ReadError("offline")
            await self.start(self.app.fetch_cases)
            await pilot.pause()
            self.assertIn(
                "Press R",
                str(
                    self.app.query_one("#case-detail-container Static", Static).render()
                ),
            )
            self.app.selected_case_id = "1"
            self.service.get_case_details.side_effect = ReadError("offline")
            await self.start(lambda: self.app.fetch_case_details("1"))
            await pilot.pause()
            self.assertIn(
                "Press R",
                str(
                    self.app.query_one("#case-detail-container Static", Static).render()
                ),
            )
            self.service.state_service.mark_case_read.assert_not_called()

    async def test_failed_same_case_refresh_cannot_acknowledge_unseen_version(self):
        async with self.app.run_test() as pilot:
            await self.show_good_view(pilot)
            self.service.get_case_details.side_effect = ReadError("unavailable")
            await self.start(lambda: self.app.fetch_case_details("1", True))
            self.app.action_mark_read()
            self.service.state_service.mark_case_read.assert_not_called()
            self.service.get_case_details.side_effect = None
            self.service.get_case_details.return_value = details(version="complete-new")
            await self.start(lambda: self.app.fetch_case_details("1", True))
            with patch.object(self.app, "run_worker"):
                self.app.action_mark_read()
            self.service.state_service.mark_case_read.assert_called_once()

    async def test_current_poll_list_and_probe_errors_warn_without_marking_read(self):
        async with self.app.run_test() as pilot:
            old, atts, widgets = await self.show_good_view(pilot)
            for phase in ("list", "probe"):
                with self.subTest(phase=phase):
                    self.service.list_cases.side_effect = (
                        ReadError("offline") if phase == "list" else None
                    )
                    self.service.list_cases.return_value = [old.case]
                    self.service.check_case_last_modified.side_effect = ReadError(
                        "probe failed"
                    )
                    await self.start(self.app.poll_cases_background)
                    self.assertFalse(self.app._poll_in_flight)
                    self.assertIs(self.app.current_details, old)
                    self.assertIs(self.app.current_attachments, atts)
                    self.assertEqual(
                        tuple(self.app.query_one("#case-detail-container").children),
                        widgets,
                    )
            self.assertEqual(len(self.warnings), 2)
            self.service.state_service.mark_case_read.assert_not_called()

    async def test_manual_mark_read_after_failed_refresh_requires_revalidation(
        self,
    ):
        async with self.app.run_test() as pilot:
            old, _, _ = await self.show_good_view(pilot)
            old.comments = [
                CaseComment("known", "body", "2026-10-14T12:00:00Z", "user")
            ]
            fresh = details(version="new")
            fresh.case.status = "Closed"
            fresh.comments = [
                CaseComment("unseen", "body", "2026-10-15T12:00:00Z", "user")
            ]
            self.service.get_case_details.return_value = fresh
            self.attachments.list_attachments.side_effect = ReadError("offline")
            await self.start(lambda: self.app.fetch_case_details("1", True))
            self.service.state_service.mark_case_read.assert_not_called()
            with patch.object(self.app, "_request_case_details"):
                self.app.action_mark_read()
            self.service.state_service.mark_case_read.assert_not_called()
            self.attachments.list_attachments.side_effect = None
            self.attachments.list_attachments.return_value = []
            self.service.get_case_details.return_value = old
            await self.start(lambda: self.app.fetch_case_details("1", True))
            with patch.object(self.app, "_request_case_details"):
                self.app.action_mark_read()
            self.service.state_service.mark_case_read.assert_called_once_with(
                user="offline",
                case_number="1",
                status=old.case.status,
                severity=old.case.severity,
                last_comment_id="known",
                last_comment_date="2026-10-14T12:00:00Z",
            )

    async def test_stale_errors_are_quiet_and_poll_always_releases(self):
        async with self.app.run_test() as pilot:
            await self.show_good_view(pilot)
            for phase in (
                "list",
                "detail",
                "poll-list",
                "poll-probe",
                "poll-detail",
                "poll-attachments",
            ):
                for change in ("selection", "generation", "list"):
                    with self.subTest(phase=phase, change=change):
                        self.app.selected_case_id = "1"
                        self.service.list_cases.side_effect = None
                        self.service.list_cases.return_value = [
                            details(
                                version="new" if phase != "poll-probe" else "old"
                            ).case
                        ]
                        self.service.get_case_details.side_effect = None
                        self.service.get_case_details.return_value = details(
                            version="new"
                        )
                        self.service.check_case_last_modified.side_effect = None
                        self.attachments.list_attachments.side_effect = None
                        fail, entered, release = self.gate_error()
                        if phase in ("list", "poll-list"):
                            self.service.list_cases.side_effect = fail
                        elif phase == "poll-probe":
                            self.service.check_case_last_modified.side_effect = fail
                        elif phase == "poll-attachments":
                            self.attachments.list_attachments.side_effect = fail
                        else:
                            self.service.get_case_details.side_effect = fail
                        if phase == "list":
                            task = self.start(self.app.fetch_cases)
                        elif phase == "detail":
                            task = self.start(lambda: self.app.fetch_case_details("1"))
                        else:
                            task = self.start(self.app.poll_cases_background)
                        await self.reached(entered)
                        if change == "selection":
                            self.app.selected_case_id = "2"
                        elif change == "list" and phase != "detail":
                            self.app._begin_case_list()
                        else:
                            self.app._detail_generation += 1
                        release.set()
                        await task
                        self.assertFalse(self.app._poll_in_flight)
                        self.assertEqual(self.warnings, [])

    async def test_failed_selection_keeps_old_widgets_and_blocks_cross_case_actions(
        self,
    ):
        async with self.app.run_test() as pilot:
            old, atts, widgets = await self.show_good_view(pilot)
            self.app.screen.remove_class("commenting")
            self.app.query_one(TextArea).text = ""
            self.service.get_case_details.side_effect = ReadError("B unavailable")
            self.app.on_row_selected(
                SimpleNamespace(row_key=SimpleNamespace(value="2"))
            )
            await self.app.workers.wait_for_complete()
            await pilot.pause()
            self.assertEqual(self.app.selected_case_id, "2")
            self.assertIs(self.app.current_details, old)
            self.assertIs(self.app.current_attachments, atts)
            self.assertEqual(
                tuple(self.app.query_one("#case-detail-container").children), widgets
            )
            with (
                patch.object(self.app, "push_screen") as modal,
                patch.object(self.app, "run_worker") as worker,
            ):
                for action in (
                    self.app.action_add_comment,
                    self.app.on_comment_post_click,
                    self.app.action_add_attachment,
                    self.app.action_apply_template,
                    self.app.action_download_attachment,
                    self.app.action_mark_read,
                ):
                    action()
                modal.assert_not_called()
                worker.assert_not_called()
            self.service.state_service.mark_case_read.assert_not_called()
            self.service.add_comment.assert_not_called()
            self.attachments.upload_attachment.assert_not_called()
            self.attachments.download_attachment.assert_not_called()


if __name__ == "__main__":
    unittest.main()
