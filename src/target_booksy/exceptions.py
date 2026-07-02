"""
target-booksy Exceptions
"""


class BooksynTargetError(Exception):
    """The base exception class for target-booksy.

    Parameters:
        msg (str): Short description of the error.
        response: Error response from the API call.
    """

    def __init__(self, msg, response=None):
        super(BooksynTargetError, self).__init__(msg)
        self.message = msg
        self.response = response

    def __str__(self):
        return repr(self.message)


class AuthenticationError(BooksynTargetError):
    """OAuth2 token request failed — bad client_id, secret, or scope."""


class TokenExpiredError(BooksynTargetError):
    """Bearer token expired mid-run — triggers re-authentication."""


class EntryPostError(BooksynTargetError):
    """POST to bc_gj_lines failed with a non-2xx response."""


class TransformError(BooksynTargetError):
    """CSV transformation failed — missing or invalid field values."""


class RateLimitError(BooksynTargetError):
    """Business Central API returned HTTP 429."""


class ServerError(BooksynTargetError):
    """Business Central API returned HTTP 5xx."""
