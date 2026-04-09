import hashlib

import diff_docs

# ---------------------------------------------------------------------------
# parse_checksums
# ---------------------------------------------------------------------------


def test_parse_checksums_missing_file(tmp_path):
    result = diff_docs.parse_checksums(str(tmp_path / "nonexistent.md"))
    assert result == {}


def test_parse_checksums_empty_file(tmp_path):
    f = tmp_path / "checksums.md"
    f.write_text("")
    assert diff_docs.parse_checksums(str(f)) == {}


def test_parse_checksums_valid(tmp_path):
    f = tmp_path / "checksums.md"
    f.write_text("abc123  file_a.pdf\ndef456  file_b.pdf\n")
    result = diff_docs.parse_checksums(str(f))
    assert result == {"abc123": "file_a.pdf", "def456": "file_b.pdf"}


def test_parse_checksums_ignores_malformed_lines(tmp_path):
    f = tmp_path / "checksums.md"
    f.write_text("abc123  file_a.pdf\nbadline\n\ndef456  file_b.pdf\n")
    result = diff_docs.parse_checksums(str(f))
    assert "abc123" in result
    assert "def456" in result
    assert len(result) == 2


# ---------------------------------------------------------------------------
# generate_checksums
# ---------------------------------------------------------------------------


def _md5(content: bytes) -> str:
    m = hashlib.md5()
    m.update(content)
    return m.hexdigest()


def test_generate_checksums_creates_entries(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"hello")
    (tmp_path / "b.pdf").write_bytes(b"world")
    output = str(tmp_path / "out.md")

    diff_docs.generate_checksums(str(tmp_path), output)

    result = diff_docs.parse_checksums(output)
    assert result[_md5(b"hello")] == "a.pdf"
    assert result[_md5(b"world")] == "b.pdf"


def test_generate_checksums_skips_checksum_file(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"hello")
    checksum_file = str(tmp_path / "000_checksumfile.md")
    # Pre-write something into the checksum file itself
    open(checksum_file, "w").close()

    diff_docs.generate_checksums(str(tmp_path), checksum_file)

    result = diff_docs.parse_checksums(checksum_file)
    # Only a.pdf should appear — the checksum file itself must be excluded
    assert len(result) == 1
    assert _md5(b"hello") in result


# ---------------------------------------------------------------------------
# comp_folders
# ---------------------------------------------------------------------------


def _setup_base_with_file(base_dir, filename, content):
    """Write a file into base_dir and generate its checksum file."""
    (base_dir / filename).write_bytes(content)
    diff_docs.generate_checksums(str(base_dir), str(base_dir / "000_checksumfile.md"))


def test_comp_folders_missing_work_dir(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    # Should return early without raising
    diff_docs.comp_folders(str(tmp_path / "nonexistent_work"), str(base))


def test_comp_folders_empty_work_dir(tmp_path, mocker):
    work = tmp_path / "work"
    work.mkdir()
    base = tmp_path / "base"
    base.mkdir()
    mocker.patch("diff_docs.progressbar")
    # Empty work folder → returns early
    diff_docs.comp_folders(str(work), str(base))
    # work dir not deleted (returned early)
    assert work.exists()


def test_comp_folders_new_file(tmp_path, mocker):
    work = tmp_path / "work"
    work.mkdir()
    base = tmp_path / "base"
    base.mkdir()
    (work / "new.pdf").write_bytes(b"new content")

    mocker.patch("diff_docs.progressbar")
    mocker.patch("diff_docs.copy_files")  # avoid creating df_* folder in cwd

    diff_docs.comp_folders(str(work), str(base))

    # work dir cleaned up
    assert not work.exists()
    # new file moved to base
    assert (base / "new.pdf").exists()
    # checksum file updated in base
    assert (base / "000_checksumfile.md").exists()


def test_comp_folders_removed_file(tmp_path, mocker):
    work = tmp_path / "work"
    work.mkdir()
    base = tmp_path / "base"
    base.mkdir()

    # Base has "old.pdf", work has "new.pdf"
    _setup_base_with_file(base, "old.pdf", b"old content")
    (work / "new.pdf").write_bytes(b"new content")

    mocker.patch("diff_docs.progressbar")
    mocker.patch("diff_docs.copy_files")

    diff_docs.comp_folders(str(work), str(base))

    assert not work.exists()
    assert not (base / "old.pdf").exists()  # removed
    assert (base / "new.pdf").exists()  # new file synced


def test_comp_folders_unchanged_file(tmp_path, mocker):
    work = tmp_path / "work"
    work.mkdir()
    base = tmp_path / "base"
    base.mkdir()

    content = b"same content"
    _setup_base_with_file(base, "doc.pdf", content)
    (work / "doc.pdf").write_bytes(content)

    mock_pb = mocker.patch("diff_docs.progressbar")  # noqa: F841
    mock_copy = mocker.patch("diff_docs.copy_files")

    diff_docs.comp_folders(str(work), str(base))

    mock_copy.assert_not_called()
    assert not work.exists()
    assert (base / "doc.pdf").exists()  # unchanged file stays in base


def test_comp_folders_renamed_same_content_no_diff(tmp_path, mocker):
    """Same content, different name → MD5 match → no diff entry produced."""
    work = tmp_path / "work"
    work.mkdir()
    base = tmp_path / "base"
    base.mkdir()

    content = b"identical bytes"
    _setup_base_with_file(base, "old_name.pdf", content)
    (work / "new_name.pdf").write_bytes(content)

    mocker.patch("diff_docs.progressbar")
    mock_copy = mocker.patch("diff_docs.copy_files")

    diff_docs.comp_folders(str(work), str(base))

    # Same MD5 in both → no diff entry → copy_files never called
    mock_copy.assert_not_called()
