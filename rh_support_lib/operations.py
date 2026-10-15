"""Reusable, presentation-free orchestration for case creation and attachments."""

from __future__ import annotations

import os


class PartialCaseCreationError(RuntimeError):
    """A case exists, but at least one requested upload is not confirmed."""

    def __init__(self, receipt: dict):
        self.receipt = receipt
        super().__init__(
            f"Case {receipt['case_number']} was created, but some attachments were not "
            "confirmed. Do not repeat case creation. Check attachments on this case "
            "before retrying uploads to the existing case."
        )


class CaseCreationOutcomeUnknownError(RuntimeError):
    """Creation returned without a usable case number; creation must not be retried blindly."""

    def __init__(self):
        super().__init__(
            "Case creation outcome is unknown: no valid case number was returned. "
            "Check the Red Hat Customer Portal before retrying. "
            "Do not repeat case creation until the outcome is verified."
        )


def create_case_with_attachments(
    case_service, attachment_service, case_fields, files=None
) -> dict:
    """Preflight files, create once, and upload each requested file at most once.

    Return a receipt on full success. PartialCaseCreationError carries the same
    receipt shape when any upload is unconfirmed, including cancellation. Creation
    exceptions propagate unchanged; an invalid returned case number instead raises
    CaseCreationOutcomeUnknownError. Neither condition triggers uploads or retries.
    Services retain responsibility for remote protocols and upload confirmation.
    """
    paths = []
    try:
        if isinstance(files, (str, bytes, os.PathLike)):
            raise TypeError
        for file in files if files is not None else ():
            path = os.fsdecode(os.fspath(file))
            if not os.path.isfile(path):
                raise ValueError
            # Check readability without loading potentially large attachments.
            with open(path, "rb"):
                pass
            paths.append(path)
    except (TypeError, ValueError, OSError):
        raise ValueError(
            "Attachments must be readable local files supplied as an iterable of paths. "
            "No case was created."
        ) from None

    case_number = case_service.create_case(**case_fields)
    if not isinstance(case_number, str) or not case_number.strip():
        raise CaseCreationOutcomeUnknownError
    case_number = case_number.strip()
    receipt = {
        "case_number": case_number,
        "created": True,
        "success": True,
        "attachments": [
            {
                "file": path,
                "uploaded": False,
                "attachment_id": None,
                "error": None,
            }
            for path in paths
        ],
    }
    for index, outcome in enumerate(receipt["attachments"]):
        try:
            attachment = attachment_service.upload_attachment(
                case_number, outcome["file"]
            )
            if getattr(attachment, "upload_status", None) != "Completed":
                outcome["error"] = "Upload was not confirmed. Check the existing case."
                receipt["success"] = False
                continue
            identifier = getattr(attachment, "uuid", None)
            outcome["attachment_id"] = (
                identifier
                if isinstance(identifier, str) and identifier.strip()
                else None
            )
            outcome["uploaded"] = True
        except KeyboardInterrupt:
            receipt["success"] = False
            outcome["error"] = "Upload canceled; completion was not confirmed."
            for remaining in receipt["attachments"][index + 1 :]:
                remaining["error"] = (
                    "Not attempted because attachment uploads were canceled."
                )
            break
        except Exception:  # noqa: BLE001
            receipt["success"] = False
            outcome["error"] = (
                "Upload failed; completion was not confirmed. Check the existing case."
            )

    # Raise outside handlers so remote exceptions cannot leak through chaining.
    if not receipt["success"]:
        raise PartialCaseCreationError(receipt)
    return receipt
