import errno
import os
import stat
import tempfile

import pytest

from k_b.core.atomic_write import write_file_atomically

TARGET_NAME = "state.json"


class FailingTemporaryFile:
    """A real temporary file whose write, flush or close fails as on a full disk; a failed flush
    leaves the data in the buffer, so the close fails again."""

    def __init__(self, temporary_file, failing_step, error):
        self.temporary_file = temporary_file
        self.failing_step = failing_step
        self.error = error
        self.name = temporary_file.name
        self.is_closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()

    def write(self, content):
        if self.failing_step == "write":
            raise self.error
        return self.temporary_file.write(content)

    def flush(self):
        if self.failing_step == "flush":
            raise self.error
        self.temporary_file.flush()

    def fileno(self):
        return self.temporary_file.fileno()

    def close(self):
        if self.is_closed:
            return
        self.is_closed = True
        self.temporary_file.close()
        if self.failing_step == "close":
            raise self.error
        if self.failing_step == "flush":
            raise OSError(errno.ENOSPC, "No space left on device")


def disk_full():
    return OSError(errno.ENOSPC, "No space left on device")


@pytest.mark.parametrize(
    ("failing_step", "error"),
    [
        ("write", disk_full()),
        ("flush", disk_full()),
        ("flush", KeyboardInterrupt()),
        ("close", disk_full()),
        ("rename", KeyboardInterrupt()),
    ],
    ids=["write", "flush", "ctrl-c-at-flush", "close", "ctrl-c-at-rename"],
)
def test_a_failed_write_leaves_no_temporary_file_and_raises_its_own_error(
    tmp_path, monkeypatch, failing_step, error
):
    target = tmp_path / TARGET_NAME
    target.write_bytes(b"previous")
    real_named_temporary_file = tempfile.NamedTemporaryFile
    monkeypatch.setattr(
        tempfile,
        "NamedTemporaryFile",
        lambda **kwargs: FailingTemporaryFile(
            real_named_temporary_file(**kwargs), failing_step, error
        ),
    )
    if failing_step == "rename":

        def interrupt_the_rename(source, target):
            raise error

        monkeypatch.setattr(os, "replace", interrupt_the_rename)

    with pytest.raises(type(error)) as raised:
        write_file_atomically(target, b"next")

    assert raised.value is error
    assert list(tmp_path.iterdir()) == [target]
    assert target.read_bytes() == b"previous"


def test_the_temporary_file_is_synced_before_the_rename_then_its_folder(
    tmp_path, monkeypatch
):
    events = []
    real_fsync = os.fsync
    real_replace = os.replace

    def record_fsync(file_descriptor):
        events.append(("fsync", os.fstat(file_descriptor).st_ino))
        real_fsync(file_descriptor)

    def record_replace(source, target):
        events.append(("replace", None))
        real_replace(source, target)

    monkeypatch.setattr(os, "fsync", record_fsync)
    monkeypatch.setattr(os, "replace", record_replace)
    target = tmp_path / TARGET_NAME

    write_file_atomically(target, b"{}")

    assert events == [
        ("fsync", target.stat().st_ino),
        ("replace", None),
        ("fsync", tmp_path.stat().st_ino),
    ]


def test_each_folder_created_for_the_write_is_synced_in_its_parent(
    tmp_path, monkeypatch
):
    synced_inodes = []
    real_fsync = os.fsync

    def record_fsync(file_descriptor):
        synced_inodes.append(os.fstat(file_descriptor).st_ino)
        real_fsync(file_descriptor)

    monkeypatch.setattr(os, "fsync", record_fsync)
    source_dir = tmp_path / "automated_watch" / "tldr_ai"
    source_dir.mkdir(parents=True)
    collection_dir = source_dir / "2026-10-07T084509Z"
    target = collection_dir / "2026-10-07.html"

    write_file_atomically(target, b"page")

    assert synced_inodes == [
        target.stat().st_ino,
        collection_dir.stat().st_ino,
        source_dir.stat().st_ino,
    ]


def fail_the_fsync_of_a_folder(error_number):
    real_fsync = os.fsync

    def fsync(file_descriptor):
        if stat.S_ISDIR(os.fstat(file_descriptor).st_mode):
            raise OSError(error_number, os.strerror(error_number))
        real_fsync(file_descriptor)

    return fsync


@pytest.mark.parametrize(
    "error_number", [errno.EBADF, errno.EINVAL], ids=["EBADF", "EINVAL"]
)
def test_a_folder_that_refuses_fsync_does_not_fail_the_write(
    tmp_path, monkeypatch, error_number
):
    monkeypatch.setattr(os, "fsync", fail_the_fsync_of_a_folder(error_number))
    target = tmp_path / "new_folder" / TARGET_NAME

    write_file_atomically(target, b"{}")

    assert target.read_bytes() == b"{}"


def test_a_failed_fsync_of_the_folder_is_raised(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "fsync", fail_the_fsync_of_a_folder(errno.EIO))

    with pytest.raises(OSError) as raised:
        write_file_atomically(tmp_path / TARGET_NAME, b"{}")

    assert raised.value.errno == errno.EIO
