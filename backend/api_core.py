"""Small shared pieces of the public API (kept apart so the route modules do not import each other)."""
import api_errors


class ApiError(Exception):
    """Raised anywhere in a /v1 handler; becomes the closed error body with the right status."""
    def __init__(self, code, headers=None):
        super().__init__(code)
        self.code = code if code in api_errors.ERRORS else "internal_error"
        self.headers = headers or {}


class Conflict(Exception):
    """The database refused an insert because the row already exists (a unique key was hit)."""
