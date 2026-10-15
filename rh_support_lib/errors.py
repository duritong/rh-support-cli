"""Safe, user-facing failures for incomplete reads and mutation outcomes."""

from __future__ import annotations

from rh_support_lib.models import CaseComment


class CommentOutcomeUnknownError(RuntimeError):
    """A comment mutation returned without a usable confirmation ID."""

    def __init__(self, case_number: str) -> None:
        self.case_number = case_number
        super().__init__(
            "Comment outcome is unknown. Verify the case in the portal. "
            "Do not repost the comment."
        )


class PartialCommentError(RuntimeError):
    """A confirmed comment was posted, but a follow-up operation failed."""

    def __init__(
        self,
        case_number: str,
        comment: CaseComment,
        requested_status: str | None,
        *,
        status_confirmed: bool = False,
        cache_invalidation_failed: bool = False,
    ) -> None:
        self.case_number = case_number
        self.comment = comment
        self.requested_status = requested_status
        self.status_confirmed = status_confirmed
        self.cache_invalidation_failed = cache_invalidation_failed
        if requested_status and not status_confirmed:
            message = (
                "Comment posted, but the requested status change was not confirmed. "
                "Do not repost the comment. Inspect the case in the portal, then change status only."
            )
        else:
            message = (
                "Comment posted, but local case cache invalidation was not confirmed. "
                "Do not repost the comment. Inspect the case in the portal."
            )
        super().__init__(message)


class ReadError(RuntimeError):
    """A required remote read could not be completed."""


class IncompleteCaseHistoryError(ReadError):
    """The requested case comment history could not be retrieved in full."""


class PartialSyncError(ReadError):
    """Successful sync updates were saved, but some cases could not be read."""

    def __init__(self, report, failures):
        self.report = report
        self.failures = tuple(failures)
        super().__init__(
            f"Sync incomplete: {report['synced_count']} cases synced; "
            f"{len(self.failures)} cases unavailable."
        )
