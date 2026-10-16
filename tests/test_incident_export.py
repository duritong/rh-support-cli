"""Incident handoffs must not invent pending action for closed/unknown states."""

import json
import unittest

from rh_support_lib.commands.show_case import format_incident_export
from rh_support_lib.models import Case


class IncidentExportTests(unittest.TestCase):
    def test_closed_and_unknown_statuses_do_not_claim_active_investigation(self):
        for status in ("Closed", "closed", "", "Unknown", "Unrecognized vendor state"):
            for fmt in ("json", "markdown", "slack"):
                with self.subTest(status=status, fmt=fmt):
                    case = Case("123", "Incident", status, "3")
                    output = format_incident_export(case, "", [], [], fmt=fmt)
                    action = (
                        json.loads(output)["next_step"] if fmt == "json" else output
                    )
                    self.assertNotIn(
                        "Waiting on Red Hat support / engineering investigation", action
                    )
                    self.assertIn(
                        "closed" if status.lower() == "closed" else "unknown",
                        action.lower(),
                    )

    def test_known_workflow_states_preserve_their_action_owner(self):
        for status, expected in (
            ("Waiting on Customer", "Customer action required"),
            ("Waiting on Customer Action Required", "Customer action required"),
            ("Waiting on Red Hat", "Waiting on Red Hat"),
            ("Waiting on Engineering", "Waiting on Red Hat"),
            ("In Progress", "Waiting on Red Hat"),
        ):
            with self.subTest(status=status):
                output = format_incident_export(
                    Case("123", "Incident", status, "3"), "", [], [], fmt="json"
                )
                self.assertIn(expected, json.loads(output)["next_step"])
