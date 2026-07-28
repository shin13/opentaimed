# path: src/taiwan_fda_mcp/__init__.py
# brief: Public package surface — re-exports stable consumer-facing API.

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _distribution_version

from taiwan_fda_mcp.exceptions import (
    AppException,
    DatasetFetchError,
    InsertFetchError,
    InsertParseError,
    InvalidLicenseError,
    LicensePrefixUnsupportedError,
    RCode,
)

# Derived from the installed distribution metadata, which hatchling builds from
# `version` in pyproject.toml — that stays the single place a release bumps.
# A hand-written literal here silently drifts instead: it sat at "0.1.0" through
# six releases up to 0.7.0, because nothing in the build or the test suite ever
# compares the two.
#
# The fallback covers importing straight from a source tree (e.g. PYTHONPATH=src
# without an install), where there is no metadata to read and an unguarded
# lookup would raise PackageNotFoundError and break `import taiwan_fda_mcp`
# outright. "0.0.0+unknown" is valid PEP 440 — so a consumer parsing it does not
# crash — while being obviously not a real release rather than a plausible
# fabricated one.
try:
    __version__ = _distribution_version("taiwan-fda-mcp")
except PackageNotFoundError:  # pragma: no cover — installed in every shipped path
    __version__ = "0.0.0+unknown"

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
