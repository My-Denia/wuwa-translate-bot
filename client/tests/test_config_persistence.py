"""The settings file has to still be there next time - and next reboot.

The defect these cover is not hypothetical. On 2026-08-12 the owner's
`%APPDATA%\\WuwaTerm\\config.json` was found MISSING after a real Windows
restart (cause unidentified; most likely an external cleanup tool - the
device credential in the Windows Credential Manager was untouched). The
client then fell back to a machine-local development address and reported a
connection failure, so the visible symptom was "the service is down" rather
than "your setting is gone".

Two halves are checked here: that a save really reaches the disk and can be
read back by a DIFFERENT process, and that a save cannot leave a partial
file behind. The third half - that the file survives a reboot - is not
runnable in CI at all, and what stands in for it is stated as exactly that
in `test_the_configuration_lives_where_a_restart_preserves_it`.

No Qt, no network, no credential store.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import wuwaterm_client
from wuwaterm_client.config import (
    APP_DIR_NAME,
    CONFIG_FILE_NAME,
    ClientConfig,
    app_data_dir,
    config_path,
)

# A separate interpreter, started fresh, reading the file this process wrote.
# It prints one JSON object; anything else it writes is a failure the test
# reports verbatim.
_CHILD_PROGRAM = """
import json
import sys
from pathlib import Path

from wuwaterm_client.config import ClientConfig

config = ClientConfig.load(Path(sys.argv[1]))
sys.stdout.write(
    json.dumps(
        {
            "base_url": config.base_url,
            "request_timeout_seconds": config.request_timeout_seconds,
            "translate_timeout_seconds": config.translate_timeout_seconds,
            "is_configured": config.is_configured,
        }
    )
)
"""


def _client_source_root() -> Path:
    """`client/src`, wherever this checkout lives.

    A child interpreter does not inherit pytest's `pythonpath` setting, and
    an editable install is not something a test should assume about the
    machine it runs on.
    """
    return Path(wuwaterm_client.__file__).resolve().parents[1]


def test_a_saved_configuration_is_really_on_disk(tmp_path: Path) -> None:
    """First save, then look at the file - not at the object in memory."""
    config = ClientConfig(
        base_url="https://api.example.invalid/wuwaterm-api",
        request_timeout_seconds=11.0,
        translate_timeout_seconds=45.0,
    )
    config.save(base_dir=tmp_path)

    path = config_path(tmp_path)
    assert path.is_file()
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "base_url": "https://api.example.invalid/wuwaterm-api",
        "request_timeout_seconds": 11.0,
        "translate_timeout_seconds": 45.0,
    }
    # Nothing else: an atomic write leaves no temporary file behind.
    assert sorted(item.name for item in tmp_path.iterdir()) == [CONFIG_FILE_NAME]


def test_a_new_process_reads_back_what_this_one_saved(tmp_path: Path) -> None:
    """The state that matters is the one a LATER launch sees.

    An in-process round trip proves the encoder and the decoder agree. It
    does not prove the bytes left this process, and the failure being
    guarded against here is precisely one where a later start-up finds
    nothing.
    """
    saved = ClientConfig(
        base_url="https://api.example.invalid",
        request_timeout_seconds=7.5,
        translate_timeout_seconds=42.0,
    )
    saved.save(base_dir=tmp_path)

    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(_client_source_root())
    completed = subprocess.run(
        [sys.executable, "-c", _CHILD_PROGRAM, str(tmp_path)],
        capture_output=True,
        text=True,
        env=environment,
        timeout=120,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "base_url": "https://api.example.invalid",
        "request_timeout_seconds": 7.5,
        "translate_timeout_seconds": 42.0,
        "is_configured": True,
    }


def test_the_configuration_lives_where_a_restart_preserves_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proxy for reboot survival, which CI cannot perform.

    A test runner cannot restart the machine, so what is pinned here is the
    property a reboot depends on: the file is derived from `%APPDATA%` - the
    roaming user profile, which a restart preserves - and from nothing else.
    A path derived from a scratch directory would pass every other test in
    this file and lose the setting on the next boot, which is the failure
    that started this work.

    REAL-REBOOT EVIDENCE IS MANUAL. On 2026-08-12 the owner restarted this
    Windows machine and inspected `%APPDATA%\\WuwaTerm\\config.json` by hand;
    that observation is what identified the missing file, and re-collecting
    it is a manual step, not something this assertion claims to have done.
    """
    roaming = tmp_path / "Roaming"
    scratch = tmp_path / "Temp"
    monkeypatch.setenv("APPDATA", str(roaming))
    for variable in ("TEMP", "TMP", "TMPDIR"):
        monkeypatch.setenv(variable, str(scratch))

    assert app_data_dir() == roaming / APP_DIR_NAME
    assert config_path() == roaming / APP_DIR_NAME / CONFIG_FILE_NAME

    # The scratch location is not an input: moving it moves nothing.
    for variable in ("TEMP", "TMP", "TMPDIR"):
        monkeypatch.setenv(variable, str(tmp_path / "Elsewhere"))
    assert config_path() == roaming / APP_DIR_NAME / CONFIG_FILE_NAME


@pytest.mark.skipif(os.name != "nt", reason="the roaming profile is a Windows path")
def test_on_this_machine_the_configuration_is_not_in_a_temporary_directory() -> None:
    """The same property, un-monkeypatched, against the real environment.

    The test above proves the derivation; this one proves the value that
    derivation actually produces here is not a disposable location.
    """
    import tempfile

    appdata = os.environ.get("APPDATA")
    assert appdata, "APPDATA is unset, so the client has no roaming profile to use"

    directory = app_data_dir().resolve()
    assert Path(appdata).resolve() in directory.parents
    assert Path(tempfile.gettempdir()).resolve() not in directory.parents


def test_a_save_that_fails_partway_leaves_the_previous_file_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole reason the write is atomic.

    A direct write truncates the target first, so a crash, a power loss or a
    full disk between that and the last byte leaves a `config.json` that
    parses as nothing - and a malformed file now costs the owner their
    server address instead of being papered over by a fallback. The failure
    is injected at the flush, which is where a full disk really surfaces.
    """
    ClientConfig(base_url="https://api.example.invalid").save(base_dir=tmp_path)
    before = config_path(tmp_path).read_text(encoding="utf-8")

    def refuse(descriptor: int) -> None:
        raise OSError("no space left on device")

    monkeypatch.setattr(os, "fsync", refuse)
    with pytest.raises(OSError):
        ClientConfig(base_url="https://other.example.invalid").save(base_dir=tmp_path)

    assert config_path(tmp_path).read_text(encoding="utf-8") == before
    assert ClientConfig.load(tmp_path).base_url == "https://api.example.invalid"
    # And no half-written temporary file accumulating in the owner's profile.
    assert sorted(item.name for item in tmp_path.iterdir()) == [CONFIG_FILE_NAME]


def test_saving_over_an_existing_file_replaces_it_whole(tmp_path: Path) -> None:
    """Repeated saves must not leave the target longer than the new content."""
    ClientConfig(
        base_url="https://api.example.invalid/a-rather-long-path-prefix"
    ).save(base_dir=tmp_path)
    ClientConfig(base_url="https://b.invalid").save(base_dir=tmp_path)

    assert json.loads(config_path(tmp_path).read_text(encoding="utf-8"))[
        "base_url"
    ] == "https://b.invalid"
    assert sorted(item.name for item in tmp_path.iterdir()) == [CONFIG_FILE_NAME]
