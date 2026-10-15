"""Safe, user-facing failures for unavailable or incomplete reads."""


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
