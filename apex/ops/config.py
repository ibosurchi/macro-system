"""H8 -- configuration and secret bootstrap for unattended runners.

TWO DISTINCT PROBLEMS, SOLVED SEPARATELY
----------------------------------------
``production_core.get_secret`` reads ``st.secrets`` and nothing else -- there is
no environment-variable fallback anywhere in it -- and that function lives in a
protected file that H8 may not modify. Unattended execution therefore has two
different needs, and conflating them would drag Streamlit into every module:

1.  **The legacy runtime needs a secrets file.** The existing bridges reach
    ``get_secret`` transitively, so a scheduler must materialise a real
    ``.streamlit/secrets.toml`` before importing them. That is
    ``materialize_streamlit_secrets``.

2.  **This package needs its own settings.** The heartbeat and the lease talk to
    Supabase directly and must work with no Streamlit runtime at all. They read
    the environment first and fall back to parsing the same TOML file with
    ``tomllib`` from the standard library. That is ``ops_settings``.

Because of (2), nothing in ``config``, ``logging``, ``heartbeat`` or ``lease``
imports Streamlit, and a bare ``python -m apex.ops check health`` works with no
Streamlit server, session or cache in existence.

SECRET VALUES ARE NEVER RETURNED BY ANYTHING PRINTABLE
------------------------------------------------------
``OpsSettings.describe()`` reports presence booleans only. No function here
returns a credential in a form intended for display, and the secrets file is
written through Python with restrictive permissions rather than echoed by a
shell -- a shell ``echo`` would put the value in the scheduler's log.
"""
from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

#: Secret NAMES this layer understands. Values never appear in this module.
ENV_SUPABASE_URL = "SUPABASE_URL"
ENV_SUPABASE_KEY = "SUPABASE_SERVICE_ROLE_KEY"
ENV_FRED_KEY = "FRED_API_KEY"
ENV_TELEGRAM_CHANNEL = "TELEGRAM_CHANNEL"

#: Every name a scheduled runner may supply. ``TELEGRAM_CHANNEL`` is a
#: NEWS-SOURCE channel name consumed by ``fetch_all_instant_news``; it is not an
#: alerting credential and no bot token belongs in this list. H8 sends no
#: message of any kind, which is what makes it structurally incapable of
#: touching client Telegram behaviour.
SUPPORTED_SECRET_NAMES: tuple[str, ...] = (
    ENV_SUPABASE_URL,
    ENV_SUPABASE_KEY,
    ENV_FRED_KEY,
    ENV_TELEGRAM_CHANNEL,
)

#: Where the existing runtime expects to find its secrets, relative to the
#: repository root. Already covered by .gitignore.
STREAMLIT_SECRETS_RELPATH = Path(".streamlit") / "secrets.toml"

#: Names required for a job to be able to write durable evidence at all.
DURABILITY_REQUIRED: tuple[str, ...] = (ENV_SUPABASE_URL, ENV_SUPABASE_KEY)


def project_root() -> Path:
    """Repository root, derived from this file rather than the process CWD.

    A scheduler may invoke the dispatcher from anywhere; resolving against the
    working directory would silently write the secrets file into the wrong
    place and leave the runtime reading an empty one.
    """
    return Path(__file__).resolve().parent.parent.parent


def _toml_secrets(path: Path) -> dict[str, str]:
    """Flat string values from a secrets TOML, using only the standard library.

    Deliberately not Streamlit's loader: importing Streamlit to read a config
    file would make every module in this package depend on a UI framework.
    Nested tables are ignored -- every name H8 uses is top-level and scalar.
    """
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
        return {}
    try:
        with open(path, "rb") as handle:
            parsed: Mapping[str, Any] = tomllib.load(handle)
    except (OSError, ValueError):
        return {}
    return {
        str(key): str(value)
        for key, value in parsed.items()
        if isinstance(value, (str, int, float, bool))
    }


def resolve_secret(name: str, *, root: Path | None = None) -> str:
    """One configuration value: environment first, then the secrets TOML.

    Environment first because that is what a scheduler supplies, and because an
    operator debugging locally should be able to override the file without
    editing it.
    """
    from_env = os.environ.get(name, "")
    if str(from_env).strip():
        return str(from_env).strip()
    base = root if root is not None else project_root()
    return str(_toml_secrets(base / STREAMLIT_SECRETS_RELPATH).get(name, "")).strip()


@dataclass(frozen=True)
class OpsSettings:
    """Resolved operational configuration. Carries values; never displays them."""

    supabase_url: str
    supabase_key: str
    fred_key: str
    telegram_channel: str

    @property
    def supabase_available(self) -> bool:
        """Whether durable storage is CONFIGURED.

        Mirrors ``production_core._supabase_enabled`` exactly -- both halves of
        the credential present -- so this layer and the bridges cannot disagree
        about whether durable storage exists. It is a configuration check, not a
        reachability check: an unreachable Supabase surfaces later, as a job
        failure or as a non-durable write.
        """
        return bool(self.supabase_url and self.supabase_key)

    def missing(self, names: tuple[str, ...]) -> tuple[str, ...]:
        """Which of ``names`` are absent. Returns NAMES, never values."""
        lookup = {
            ENV_SUPABASE_URL: self.supabase_url,
            ENV_SUPABASE_KEY: self.supabase_key,
            ENV_FRED_KEY: self.fred_key,
            ENV_TELEGRAM_CHANNEL: self.telegram_channel,
        }
        return tuple(name for name in names if not lookup.get(name, ""))

    def describe(self) -> dict[str, bool]:
        """Presence booleans, safe to log. Never the values themselves."""
        return {
            "supabase_url_present": bool(self.supabase_url),
            "supabase_key_present": bool(self.supabase_key),
            "fred_key_present": bool(self.fred_key),
            "telegram_channel_present": bool(self.telegram_channel),
        }


def ops_settings(*, root: Path | None = None) -> OpsSettings:
    """Resolve configuration without importing Streamlit."""
    base = root if root is not None else project_root()
    return OpsSettings(
        supabase_url=resolve_secret(ENV_SUPABASE_URL, root=base).rstrip("/"),
        supabase_key=resolve_secret(ENV_SUPABASE_KEY, root=base),
        fred_key=resolve_secret(ENV_FRED_KEY, root=base),
        telegram_channel=resolve_secret(ENV_TELEGRAM_CHANNEL, root=base),
    )


def _toml_escape(value: str) -> str:
    """Escape a value for a TOML basic string."""
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


def materialize_streamlit_secrets(
    *, root: Path | None = None, overwrite: bool = False
) -> tuple[Path, tuple[str, ...]]:
    """Write ``.streamlit/secrets.toml`` from the environment, for legacy code.

    Returns the path and the NAMES written -- never the values.

    Only runs when the environment actually carries something: a developer whose
    file already exists keeps it untouched unless ``overwrite`` is set, so an
    accidental invocation cannot destroy a local configuration.

    ``B2_SHADOW_ENABLED`` is deliberately NOT written. The one-writer cutover
    works precisely because the Streamlit deployment and this runner read
    independent secret stores: setting it here would couple them again and let a
    scheduler silently disable itself.

    The file is written through Python, never through a shell, so no value can
    reach a scheduler's log via command echo. Permissions are tightened to owner
    read/write where the platform supports it.
    """
    base = root if root is not None else project_root()
    target = base / STREAMLIT_SECRETS_RELPATH

    present = {
        name: os.environ.get(name, "").strip()
        for name in SUPPORTED_SECRET_NAMES
        if str(os.environ.get(name, "")).strip()
    }
    if not present:
        return target, ()
    if target.exists() and not overwrite:
        return target, ()

    target.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f'{name} = "{_toml_escape(value)}"' for name, value in present.items())
    target.write_text(body + "\n", encoding="utf-8")
    try:
        os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)
    except (OSError, NotImplementedError):
        # Windows and some mounts do not honour POSIX modes. The file is still
        # gitignored and lives only inside an ephemeral workspace.
        pass
    return target, tuple(sorted(present))


def remove_streamlit_secrets(*, root: Path | None = None) -> bool:
    """Delete the materialised secrets file. Safe to call when absent."""
    base = root if root is not None else project_root()
    target = base / STREAMLIT_SECRETS_RELPATH
    try:
        target.unlink()
        return True
    except (OSError, FileNotFoundError):
        return False


def code_version() -> str:
    """Short identity of the code that produced a run.

    Reads ``.git/HEAD`` directly rather than shelling out to git: a scheduler's
    container may not have git on PATH, and spawning a subprocess from an
    unattended job to learn its own version is more failure surface than the
    answer is worth. Falls back to the package version.
    """
    from . import OPS_VERSION

    for name in ("GITHUB_SHA", "GIT_COMMIT"):
        value = os.environ.get(name, "").strip()
        if value:
            return f"{OPS_VERSION}+{value[:12]}"
    try:
        head = (project_root() / ".git" / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref:"):
            ref = head.split(" ", 1)[1].strip()
            head = (project_root() / ".git" / ref).read_text(encoding="utf-8").strip()
        if head:
            return f"{OPS_VERSION}+{head[:12]}"
    except (OSError, IndexError, ValueError):
        pass
    return OPS_VERSION


__all__ = [
    "DURABILITY_REQUIRED",
    "ENV_FRED_KEY",
    "ENV_SUPABASE_KEY",
    "ENV_SUPABASE_URL",
    "ENV_TELEGRAM_CHANNEL",
    "STREAMLIT_SECRETS_RELPATH",
    "SUPPORTED_SECRET_NAMES",
    "OpsSettings",
    "code_version",
    "materialize_streamlit_secrets",
    "ops_settings",
    "project_root",
    "remove_streamlit_secrets",
    "resolve_secret",
]
