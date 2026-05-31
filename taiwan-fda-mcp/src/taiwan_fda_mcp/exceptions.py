# path: src/taiwan_fda_mcp/exceptions.py
# brief: Application exception hierarchy + RCode enum.

from enum import IntEnum


class RCode(IntEnum):
    """Application result codes — used for structured error responses."""

    OK = 0
    INVALID_LICENSE = 1001
    LICENSE_PREFIX_UNSUPPORTED = 1002
    SEARCH_NO_CRITERIA = 1003
    DATASET_FETCH_FAILED = 2001
    DATASET_PARSE_FAILED = 2002
    INSERT_FETCH_FAILED = 3001
    INSERT_PARSE_FAILED = 3002
    INSERT_NOT_FOUND = 3003


class AppException(Exception):
    """Base application exception carrying an RCode and a user-facing message."""

    def __init__(self, code: RCode, message: str, *, detail: object | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail

    def __repr__(self) -> str:
        return f"AppException(code={self.code.name}, message={self.message!r})"


class InvalidLicenseError(AppException):
    """Raised when a license number string cannot be parsed."""


class LicensePrefixUnsupportedError(AppException):
    """Raised when a license prefix is not in the verified mapping table."""


class DatasetFetchError(AppException):
    """Raised when downloading or unpacking an opendata dataset fails."""


class InsertFetchError(AppException):
    """Raised when the GetDrugDoc API call fails."""


class InsertParseError(AppException):
    """Raised when GetDrugDoc XML cannot be parsed."""
