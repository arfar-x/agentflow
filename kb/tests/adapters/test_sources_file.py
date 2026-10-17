"""kb-sources.yaml as a file the console rewrites in place."""

from __future__ import annotations

import os

import pytest

from kb.adapters.outbound.sources_file import FileSourcesFile
from kb.application.ports.source_files import FileChanged, FileNotWritable


def test_a_write_keeps_the_same_file_so_a_bind_mount_still_sees_it(tmp_path):
    # Covers: FR-CFG-10
    # Writing a neighbour and renaming it over the original would give a
    # single-file bind mount a stale inode.
    path = tmp_path / "kb-sources.yaml"
    path.write_text("approved: true\n", encoding="utf-8")
    inode = os.stat(path).st_ino
    file = FileSourcesFile(path)

    written = file.write("approved: false\n", expected_etag=file.snapshot().etag)

    assert os.stat(path).st_ino == inode
    assert path.read_text(encoding="utf-8") == "approved: false\n"
    assert written.etag == file.snapshot().etag


def test_a_shorter_text_does_not_leave_the_old_tail_behind(tmp_path):
    # Covers: FR-CFG-10
    path = tmp_path / "kb-sources.yaml"
    path.write_text("x" * 500, encoding="utf-8")
    file = FileSourcesFile(path)
    file.write("short\n", expected_etag=file.snapshot().etag)
    assert path.read_text(encoding="utf-8") == "short\n"


def test_the_fingerprint_follows_the_exact_bytes(tmp_path):
    # Covers: FR-CFG-10
    path = tmp_path / "kb-sources.yaml"
    path.write_text("a\n", encoding="utf-8")
    first = FileSourcesFile(path).snapshot().etag
    path.write_text("a\n", encoding="utf-8")
    assert FileSourcesFile(path).snapshot().etag == first
    path.write_text("a \n", encoding="utf-8")
    assert FileSourcesFile(path).snapshot().etag != first


def test_a_write_against_a_stale_fingerprint_is_refused_and_changes_nothing(tmp_path):
    # Covers: FR-CFG-10
    path = tmp_path / "kb-sources.yaml"
    path.write_text("one\n", encoding="utf-8")
    file = FileSourcesFile(path)
    etag = file.snapshot().etag
    path.write_text("two\n", encoding="utf-8")
    with pytest.raises(FileChanged):
        file.write("three\n", expected_etag=etag)
    assert path.read_text(encoding="utf-8") == "two\n"


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write anything")
def test_a_file_the_console_may_not_write_says_so(tmp_path):
    # Covers: FR-CFG-10
    path = tmp_path / "kb-sources.yaml"
    path.write_text("one\n", encoding="utf-8")
    path.chmod(0o444)
    file = FileSourcesFile(path)
    assert file.writable() is False
    with pytest.raises(FileNotWritable, match="KB_ADMIN_USER"):
        file.write("two\n", expected_etag=file.snapshot().etag)
