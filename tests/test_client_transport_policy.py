"""Repository gate: the desktop client's transport design cannot regress
into a host-administration channel, and cannot stop verifying certificates.

The client reaches the service at a configured secure endpoint and
authenticates every request with a device credential. Host shell access is
the operator's administration channel; it is not how the application talks
to the service, and no shipped file may teach otherwise.

These are text gates, deliberately in the repository suite rather than the
client's own: they cover files from three different trees (client sources,
client tests, the client guide, the Compose file), and they must run on
every pull request without a Windows runner or the client's dependencies.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# (a) The scan scope is the UNION of the shipped client surface and the
#     deployment file that describes how it is reached.
SCANNED_TREES = (
    ROOT / "client" / "src",
    ROOT / "client" / "tests",
)
SCANNED_FILES = (
    ROOT / "client" / "README.md",
    # The packaged entry point, the build spec and the build script: they are
    # shipped client surface too, and none of them lives under client/src.
    ROOT / "client" / "main.py",
    ROOT / "client" / "WuwaTerm.spec",
    ROOT / "client" / "build.ps1",
    # The runbook. tests/test_deploy_scripts.py pins four literal recipes in
    # it; this adds the pattern scan, so a spelling those literals miss
    # (`ssh -fNL`, `autossh`, prose) is caught as well.
    ROOT / "docs" / "deployment.md",
    *sorted((ROOT / "deploy").glob("*.yml")),
    *sorted((ROOT / "deploy").glob("*.yaml")),
)
SCANNED_SUFFIXES = {".py", ".md", ".yml", ".yaml", ".spec", ".ps1", ".txt", ".toml"}

# A local port-forwarding recipe is the specific thing being kept out, but
# the words that introduce one are what a reader copies, so both are matched.
FORWARDING_TOKENS = re.compile(
    r"""
    \bssh\b | \bsshd\b | \bautossh\b       # the administration channel
    | \btunnel\w*                          # and the design it used to justify
    | \bport[-\s]?forward\w*
    | \bLocalForward\b
    | -N\s+-L\b | \bssh\s+-\w*L\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

# The one place the client's documentation may name the administration
# channel: a note that exists precisely to say it is NOT the client's path.
# Pinned verbatim, so widening it is an edit to this list and not a side
# effect of rewording a paragraph.
ALLOWED_OPERATIONS_NOTES: dict[str, tuple[str, ...]] = {
    "client/README.md": (
        "> Operations note: SSH is how an operator administers the server host. It is",
    ),
}


def _scanned_paths() -> list[Path]:
    paths: list[Path] = []
    for tree in SCANNED_TREES:
        for path in sorted(tree.rglob("*")):
            if path.is_file() and path.suffix in SCANNED_SUFFIXES:
                paths.append(path)
    paths.extend(path for path in SCANNED_FILES if path.is_file())
    assert paths, "the scan found no files at all, which would pass vacuously"
    return paths


def _offending_lines(relative: str, text: str) -> list[str]:
    """Every line carrying a forwarding token, minus the allowlisted ones."""
    allowed = ALLOWED_OPERATIONS_NOTES.get(relative, ())
    offences = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not FORWARDING_TOKENS.search(line):
            continue
        if line.strip() in allowed:
            continue
        offences.append(f"{relative}:{number}: {line.strip()}")
    return offences


def test_the_shipped_client_surface_names_no_forwarding_path() -> None:
    """AC20. The client must not depend on, document, or advertise a
    host-administration channel as the way it reaches the service."""
    offences: list[str] = []
    for path in _scanned_paths():
        offences.extend(
            _offending_lines(
                path.relative_to(ROOT).as_posix(), path.read_text(encoding="utf-8")
            )
        )

    assert not offences, (
        "the client surface may not present a host-administration channel as "
        "its path to the service:\n" + "\n".join(offences)
    )


def test_the_scan_actually_covers_the_files_it_claims_to() -> None:
    """A gate whose scope quietly shrinks stops being a gate. These four
    files carried the revoked wording and must always be in scope."""
    scanned = {path.relative_to(ROOT).as_posix() for path in _scanned_paths()}
    for required in (
        "client/src/wuwaterm_client/api.py",
        "client/src/wuwaterm_client/config.py",
        "client/src/wuwaterm_client/strings.py",
        "client/tests/test_api.py",
        "client/README.md",
        "client/main.py",
        "client/WuwaTerm.spec",
        "client/build.ps1",
        "docs/deployment.md",
        "deploy/docker-compose.yml",
    ):
        assert required in scanned, required


@pytest.mark.parametrize(
    "line",
    [
        "ssh -N -L 8787:127.0.0.1:8787 user@host",
        "# the owner desktop reaches it through the existing SSH entry point",
        "With the tunnel open, set the client's server address",
        "set up a port-forward from this computer",
        "LocalForward 8787 127.0.0.1:8787",
    ],
)
def test_the_scanner_catches_the_wording_it_exists_for(line: str) -> None:
    """The gate is only worth its green tick if it is known to fail. Each of
    these is a real sentence that was on the shipped surface before this
    change, run through the same function the repository scan uses.

    The second case also shows the allowlist is a per-line exemption and not
    a per-file one: the same file that carries the operations note still
    fails on any other line.
    """
    assert _offending_lines("client/README.md", line) == [
        f"client/README.md:1: {line.strip()}"
    ]


def test_the_allowlisted_operations_note_is_present_and_singular() -> None:
    """An allowlist that no longer matches anything is dead configuration,
    and an allowlist that grows without review is the hole itself."""
    assert len(ALLOWED_OPERATIONS_NOTES) == 1
    for relative, lines in ALLOWED_OPERATIONS_NOTES.items():
        text = (ROOT / relative).read_text(encoding="utf-8")
        stripped = {line.strip() for line in text.splitlines()}
        for allowed in lines:
            assert allowed in stripped, f"{relative}: {allowed}"
        # The note says what it is for; without this it could be reduced to a
        # bare mention and still pass.
        assert "not part of this client's path" in text


# (c) Certificate verification: not a setting, not a flag, not anywhere.
#
# This regex is a TRIPWIRE, not the guarantee. Verification can be weakened in
# ways no text scan will see (`CERT_OPTIONAL`, a setattr, an indirection), and
# what actually proves the property is
# client/tests/test_transport_security.py::
# test_certificate_verification_is_on_for_the_client_it_actually_builds, which
# inspects the SSL context the client hands to its connection pool. This gate
# catches the obvious edit early and in the repository suite, where it runs on
# every pull request without the client's dependencies.

INSECURE_TLS = re.compile(
    r"""
    verify\s*=\s*False
    | check_hostname\s*=\s*False
    | CERT_NONE
    | VERIFY_NONE
    | _create_unverified_context
    | curl\s+(-\w*k\b|--insecure)
    """,
    re.VERBOSE,
)

TLS_SCANNED_TREES = (
    ROOT / "client" / "src",
    ROOT / "client" / "tests",
    ROOT / "src",
    ROOT / "scripts",
)

# Two lines name weakened verification in order to keep it out: the comment
# that explains the hazard, and the negative test that builds an unverified
# transport and asserts the client refuses it. Pinned verbatim, like the
# operations note above, so an exemption is a decision and not a side effect.
ALLOWED_INSECURE_TLS_LINES: dict[str, tuple[str, ...]] = {
    "client/src/wuwaterm_client/api.py": (
        "`AsyncHTTPTransport(verify=False)` would otherwise slip past a guarantee",
    ),
    "client/tests/test_transport_security.py": (
        "unverified = httpx.AsyncHTTPTransport(verify=False)",
    ),
}


def _insecure_tls_offences(relative: str, text: str) -> list[str]:
    allowed = ALLOWED_INSECURE_TLS_LINES.get(relative, ())
    offences = []
    for number, line in enumerate(text.splitlines(), start=1):
        if INSECURE_TLS.search(line) and line.strip() not in allowed:
            offences.append(f"{relative}:{number}: {line.strip()}")
    return offences


def _tls_scanned_paths() -> list[Path]:
    paths: list[Path] = []
    for tree in TLS_SCANNED_TREES:
        paths.extend(sorted(tree.rglob("*.py")))
    # The shell scripts too: `curl --insecure` is in the pattern above, and a
    # Python-only scan could never have matched it.
    paths.extend(sorted((ROOT / "deploy").glob("*.sh")))
    return paths


def test_nothing_turns_certificate_verification_off() -> None:
    offences: list[str] = []
    scanned = 0
    for path in _tls_scanned_paths():
        scanned += 1
        offences.extend(
            _insecure_tls_offences(
                path.relative_to(ROOT).as_posix(),
                path.read_text(encoding="utf-8"),
            )
        )
    assert scanned, "the scan found no files, which would pass vacuously"
    assert not offences, "certificate verification may not be weakened:\n" + "\n".join(
        offences
    )


def test_the_tls_scanner_catches_a_real_disabling_line() -> None:
    """Including in the two files that hold an exemption: the exemption is
    one pinned line, not a licence for the file."""
    assert _insecure_tls_offences(
        "client/src/wuwaterm_client/api.py",
        "        self._client = httpx.AsyncClient(verify=False)\n",
    ) == ["client/src/wuwaterm_client/api.py:1: self._client = httpx.AsyncClient(verify=False)"]
    assert _insecure_tls_offences("src/wuwaterm/sentence.py", "ctx.check_hostname = False\n")


def test_the_tls_scan_covers_the_shell_scripts_too() -> None:
    """`curl --insecure` cannot live in a .py file; a scan that only read
    Python would have carried a pattern it could never match."""
    scanned = {path.relative_to(ROOT).as_posix() for path in _tls_scanned_paths()}
    for required in (
        "client/src/wuwaterm_client/api.py",
        "deploy/vps-update.sh",
        "deploy/entrypoint.sh",
    ):
        assert required in scanned, required
    assert _insecure_tls_offences("deploy/vps-update.sh", 'curl -sk "$url"\n')


def test_the_tls_exemptions_still_describe_real_lines() -> None:
    """An exemption whose line is gone is dead configuration, and an
    exemption list that grows without review is the hole itself."""
    assert len(ALLOWED_INSECURE_TLS_LINES) == 2
    for relative, lines in ALLOWED_INSECURE_TLS_LINES.items():
        present = {
            line.strip()
            for line in (ROOT / relative).read_text(encoding="utf-8").splitlines()
        }
        for allowed in lines:
            assert allowed in present, f"{relative}: {allowed}"


def test_the_client_asks_httpx_for_verification_in_writing() -> None:
    """Removal of the explicit request has to be a visible edit."""
    api = (ROOT / "client" / "src" / "wuwaterm_client" / "api.py").read_text(
        encoding="utf-8"
    )
    assert "verify=True" in api
    assert "trust_env=False" in api


# D5': the client is an HTTP client and nothing else.

PROCESS_AND_KEY_TOKENS = re.compile(
    r"""
    \bimport\s+subprocess\b | \bfrom\s+subprocess\s+import\b
    | \bos\.system\b | \bos\.exec\w*\b | \bos\.spawn\w*\b
    | \bimport\s+paramiko\b | \bimport\s+asyncssh\b | \bimport\s+fabric\b
    | \bQProcess\b
    | \bid_rsa\b | \bid_ed25519\b | \bknown_hosts\b | \bPRIVATE\s+KEY\b
    """,
    re.VERBOSE,
)


def test_the_client_starts_no_processes_and_carries_no_keys() -> None:
    """The application reaches the service by making HTTP requests. It does
    not start helpers, and it holds exactly one credential - the device
    token, in the OS credential store - and no key material of its own."""
    offences: list[str] = []
    for path in sorted((ROOT / "client" / "src").rglob("*.py")):
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if PROCESS_AND_KEY_TOKENS.search(line):
                offences.append(
                    f"{path.relative_to(ROOT).as_posix()}:{number}: {line.strip()}"
                )
    assert not offences, "the client may not start processes or hold keys:\n" + "\n".join(
        offences
    )
