"""Application errors that become friendly API responses."""


class AppError(Exception):
    """Expected failure (bad input, upstream map service, and similar)."""

    def __init__(self, message: str, status_code: int = 400, code: str = "error"):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
