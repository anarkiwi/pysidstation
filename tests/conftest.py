"""Shared pytest fixtures.

The reference preset bank is Elektron's copyrighted factory content, so it is
**not** committed to this repository.  Instead it is fetched on demand from
Elektron's official SidStation Sound Pack archive, verified by SHA-256, and
cached locally for subsequent runs.

Offline / restricted environments: set ``SIDSTATION_PRESETS`` to the path of a
local ``SidStation_Presets_r1.syx`` to skip the download.  If the file can be
neither found locally nor downloaded, the dependent tests are skipped (not
failed) with an explanatory message.
"""

from __future__ import annotations

import hashlib
import io
import os
import pathlib
import time
import urllib.error
import urllib.request
import zipfile

import pytest

# Elektron's official SidStation patch archive, linked from the SidStation
# support-downloads page.  It bundles the factory pack plus three bonus packs.
PRESETS_ZIP_URL = "https://www.elektron.se/wp-content/uploads/2024/09/SidStation_patches.zip"
# The member we use as the reference bank (the factory pack) and its SHA-256.
PRESETS_MEMBER = "SidStation_Presets_r1.syx"
PRESETS_SHA256 = "c5602e2b866a6e66514e2bbc3fe1fe8f0b7629c6daa0774d217ab5c7f084a41f"

# Local cache location (git-ignored). Also where actions/cache restores it.
CACHE_PATH = pathlib.Path(__file__).parent / "data" / PRESETS_MEMBER


class _FetchError(Exception):
    """The archive could not be retrieved from Elektron (network/transient)."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_cached() -> bytes | None:
    """Return cached bytes from the env override or the cache path, if valid."""
    candidates = []
    override = os.environ.get("SIDSTATION_PRESETS")
    if override:
        candidates.append(pathlib.Path(override))
    candidates.append(CACHE_PATH)
    for path in candidates:
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if _sha256(data) == PRESETS_SHA256:
            return data
    return None


def _fetch_zip(attempts: int = 3) -> bytes:
    """Download the preset archive, retrying briefly on network errors."""
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(
                PRESETS_ZIP_URL, headers={"User-Agent": "pysidstation-tests"}
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read()
        except (urllib.error.URLError, OSError) as exc:
            last = exc
            if attempt < attempts - 1:
                time.sleep(2)
    raise _FetchError(f"could not download {PRESETS_ZIP_URL} ({type(last).__name__}: {last})")


def _try_cache(data: bytes) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_bytes(data)
    except OSError:
        pass  # caching is best-effort


@pytest.fixture(scope="session")
def presets_bytes() -> bytes:
    """Bytes of Elektron's factory preset bank, fetched/cached on demand."""
    cached = _load_cached()
    if cached is not None:
        return cached
    try:
        archive = _fetch_zip()
    except _FetchError as exc:
        pytest.skip(
            f"{exc}; set SIDSTATION_PRESETS to a local {PRESETS_MEMBER} to run these tests offline"
        )
    # A missing member or wrong hash means the upstream archive changed — fail
    # loudly rather than skipping, so it gets noticed.
    with zipfile.ZipFile(io.BytesIO(archive)) as archive_zip:
        data = archive_zip.read(PRESETS_MEMBER)
    digest = _sha256(data)
    assert digest == PRESETS_SHA256, (
        f"{PRESETS_MEMBER} from {PRESETS_ZIP_URL} has unexpected SHA-256 "
        f"{digest} (expected {PRESETS_SHA256})"
    )
    _try_cache(data)
    return data


@pytest.fixture(scope="session")
def presets_path(tmp_path_factory, presets_bytes) -> pathlib.Path:
    """Filesystem path to the preset bank (cache path, or a temp copy)."""
    try:
        if _sha256(CACHE_PATH.read_bytes()) == PRESETS_SHA256:
            return CACHE_PATH
    except OSError:
        pass
    path = tmp_path_factory.mktemp("presets") / PRESETS_MEMBER
    path.write_bytes(presets_bytes)
    return path
