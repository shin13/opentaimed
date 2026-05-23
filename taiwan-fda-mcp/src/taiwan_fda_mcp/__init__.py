# path: src/taiwan_fda_mcp/__init__.py
# brief: Public package surface — re-exports stable consumer-facing API.

from taiwan_fda_mcp.exceptions import (
    AppException,
    DatasetFetchError,
    InsertFetchError,
    InsertParseError,
    InvalidLicenseError,
    LicensePrefixUnsupportedError,
    RCode,
)

__version__ = "0.1.0"

__all__ = [
    "AppException",
    "DatasetFetchError",
    "InsertFetchError",
    "InsertParseError",
    "InvalidLicenseError",
    "LicensePrefixUnsupportedError",
    "RCode",
    "__version__",
]
