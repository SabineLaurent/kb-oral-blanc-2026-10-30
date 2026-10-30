"""The one function that writes a file K-B writes itself (conventions.md, rule 2)."""

import contextlib
import errno
import itertools
import os
import tempfile
from pathlib import Path

# The errors of a file system that does not support the sync of a folder: EINVAL (fsync(2)),
# EBADF on some systems. PostgreSQL tolerates the same two, EINVAL since bug #15636 on Linux
# CIFS (https://www.postgresql.org/message-id/E1gxrcU-0002A8-17%40gemulon.postgresql.org,
# read on 2026-10-16). Any other error is raised: the rename is not known to be on disk.
REFUSED_FOLDER_SYNC_ERRORS = (errno.EBADF, errno.EINVAL)


def write_file_atomically(path: Path, content: bytes) -> None:
    """Write to a hidden temporary file of the target folder, sync it, rename it, then sync the
    folders: a reader never sees a half-written file, and a power cut cannot keep a later file
    while losing this one (conventions.md, rule 2)."""
    # The folder of the target, then each new parent up to the first that exists: each holds
    # the entry of a new file or folder.
    folders_to_sync = [path.parent]
    folders_to_sync += [
        folder.parent
        for folder in itertools.takewhile(
            lambda folder: not folder.exists(), path.parents
        )
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
    ) as temporary_file:
        try:
            temporary_file.write(content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
            temporary_file.close()
            os.replace(temporary_file.name, path)
        except BaseException:
            # The original error is raised. A failed flush leaves data in the buffer: closed
            # here, the file does not fail again on leaving the with block. The temporary file
            # may already be renamed (Ctrl-C right after os.replace) or not removable.
            with contextlib.suppress(OSError):
                temporary_file.close()
            with contextlib.suppress(OSError):
                os.remove(temporary_file.name)
            raise
    for folder in folders_to_sync:
        folder_descriptor = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(folder_descriptor)
        except OSError as error:
            if error.errno not in REFUSED_FOLDER_SYNC_ERRORS:
                raise
        finally:
            os.close(folder_descriptor)
