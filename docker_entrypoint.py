"""Prepare bind-mounted storage, then permanently drop privileges before starting Flask.

Docker creates missing host bind-mount directories as root. The image's USER and
baked-in /data permissions cannot change ownership of that host directory.
Keep this entrypoint root-only for the brief initialization step; the Gunicorn
master and workers always run as the unprivileged tracker account (UID 10001).
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

TRACKER_UID = 10001
TRACKER_GID = 10001
DATA_PATH = Path('/data')


def _storage_path() -> Path:
    """Limit privileged changes to the one documented, mounted data directory."""
    requested = os.path.abspath(os.environ.get('DATA_DIR', '/data'))
    if requested != str(DATA_PATH):
        raise RuntimeError('DATA_DIR must be /data in the Docker image; refusing to change other paths')
    if DATA_PATH.is_symlink():
        raise RuntimeError('/data must be a directory, not a symlink')
    return DATA_PATH


def _repair_entry(path: Path, uid: int, gid: int, directory: bool) -> None:
    """Repair owner-only permissions with file descriptors; never follow links."""
    info = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(info.st_mode):
        return
    desired = 0o700 if directory else 0o600
    if (info.st_uid == uid and info.st_gid == gid and
            stat.S_IMODE(info.st_mode) & desired == desired):
        return

    # O_NOFOLLOW + fchown/fchmod avoids changing a symlink target even if a
    # file is replaced between the initial stat and the permission repair.
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    if directory:
        flags |= os.O_DIRECTORY
    fd = os.open(path, flags)
    try:
        current = os.fstat(fd)
        if not expected(current.st_mode):
            return
        if current.st_uid != uid or current.st_gid != gid:
            os.fchown(fd, uid, gid)
        mode = stat.S_IMODE(current.st_mode)
        if mode & desired != desired:
            os.fchmod(fd, mode | desired)
    finally:
        os.close(fd)


def prepare_storage(path: Path, uid: int = TRACKER_UID, gid: int = TRACKER_GID) -> None:
    """Repair /data and older files copied onto a root-owned host bind mount.

    This intentionally skips symlinks: never change ownership outside /data.
    Scanning files only on startup also handles restoring older SQLite backups
    and artwork from a different host/user without destroying their contents.
    """
    path.mkdir(parents=True, exist_ok=True)
    _repair_entry(path, uid, gid, directory=True)
    for current, folders, files in os.walk(path, topdown=True, followlinks=False):
        current_path = Path(current)
        folders[:] = [folder for folder in folders if not (current_path / folder).is_symlink()]
        for folder in folders:
            _repair_entry(current_path / folder, uid, gid, directory=True)
        for file in files:
            _repair_entry(current_path / file, uid, gid, directory=False)


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit('No application command provided to docker_entrypoint.py')
    try:
        path = _storage_path()
        if os.geteuid() == 0:
            prepare_storage(path)
            # Clear supplementary groups, then drop both identities permanently.
            os.setgroups([])
            os.setgid(TRACKER_GID)
            os.setuid(TRACKER_UID)
        if not path.is_dir() or not os.access(path, os.W_OK | os.X_OK):
            raise RuntimeError(f'{path} is not writable by application UID {os.geteuid()}')
    except OSError as exc:
        raise SystemExit(
            f'ACNH Tracker storage initialization failed: {exc}. '
            'Check that /data is a writable local bind mount; on NFS or '
            'rootless/user-namespaced Docker, the host may block ownership fixes.'
        ) from exc
    except RuntimeError as exc:
        raise SystemExit(f'ACNH Tracker storage initialization failed: {exc}') from exc

    os.umask(0o077)
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == '__main__':
    main()
