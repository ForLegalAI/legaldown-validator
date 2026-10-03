"""``save``: writing a document to a file, the mirror of ``load``."""
from __future__ import annotations

import os
import stat
import sys
import warnings
from pathlib import Path

import pytest

import legaldown
from legaldown import Document, load, parse, save, serialize, validate

_SOURCE = "---\ntitle: Services Agreement\nlanguage: en\n---\n\n# Scope\n\nThe Supplier provides the services.\n"


def _write(path: Path, text: str = _SOURCE) -> Path:
    path.write_bytes(text.encode("utf-8"))
    return path


def _leftovers(directory: Path, keep: set[str]) -> list[str]:
    return sorted(entry.name for entry in directory.iterdir() if entry.name not in keep)


# the round trip --------------------------------------------------------------

def test_a_loaded_document_edited_and_saved_loads_back_equal(tmp_path):
    file = _write(tmp_path / "contract.lgd")
    document = load(file)
    document.metadata.governing_law = "Czech Republic"
    document.metadata.title = "Amended Agreement"
    save(document)
    again = load(file)
    assert again == document
    assert again.metadata.governing_law == "Czech Republic"
    assert again.path == file.absolute()
    assert file.read_text(encoding="utf-8") == serialize(document)


def test_save_without_a_path_writes_back_to_where_the_document_was_loaded_from(tmp_path):
    file = _write(tmp_path / "contract.lgd")
    document = load(file)
    document.sections[0].title = "Subject"
    assert save(document) == file.absolute()
    assert load(file).sections[0].title == "Subject"
    assert _leftovers(tmp_path, {"contract.lgd"}) == []


def test_save_to_a_new_path_moves_the_document_there(tmp_path):
    original = _write(tmp_path / "contract.lgd")
    document = load(original)
    copy = tmp_path / "copy.lgd"
    assert save(document, copy) == copy.absolute()
    assert document.path == copy.absolute()
    assert original.read_text(encoding="utf-8") == _SOURCE  # the first file is untouched
    assert load(copy).sections == document.sections
    assert load(copy).metadata == document.metadata
    document.metadata.title = "Later"
    save(document)  # now writes to the copy
    assert load(copy).metadata.title == "Later"
    assert original.read_text(encoding="utf-8") == _SOURCE


def test_a_document_parsed_from_text_has_no_path_to_save_to(tmp_path):
    document = parse(_SOURCE)
    with pytest.raises(ValueError, match="no path; pass one"):
        save(document)
    assert document.path is None
    target = tmp_path / "new.lgd"
    save(document, target)
    assert document.path == target.absolute()
    assert document.filename == "new.lgd"  # as load names it: diagnostics name the file
    assert load(target) == document


def test_a_document_built_in_code_has_no_path_to_save_to():
    with pytest.raises(ValueError, match="no path; pass one"):
        save(legaldown.empty_document())


def test_the_path_returned_and_set_is_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    document = parse(_SOURCE)
    result = save(document, "relative.lgd")
    assert isinstance(result, Path)
    assert result.is_absolute()
    assert result == tmp_path / "relative.lgd"
    assert document.path == result
    assert (tmp_path / "relative.lgd").exists()


def test_a_str_and_a_path_like_are_accepted(tmp_path):
    document = parse(_SOURCE)
    assert save(document, str(tmp_path / "a.lgd")) == tmp_path / "a.lgd"

    class Like:
        def __fspath__(self) -> str:
            return str(tmp_path / "b.lgd")

    assert save(document, Like()) == tmp_path / "b.lgd"
    assert (tmp_path / "a.lgd").read_bytes() == (tmp_path / "b.lgd").read_bytes()


def test_missing_directories_are_created(tmp_path):
    target = tmp_path / "a" / "b" / "contract.lgd"
    save(parse(_SOURCE), target)
    assert load(target).metadata.title == "Services Agreement"


def test_a_saved_document_is_validated_against_files_beside_its_new_path(tmp_path):
    amendment = (
        "---\ntitle: A\namends:\n  title: Original\n  file: original.lgd\n---\n\n# S\n\nUses {{term: services}}.\n"
    )
    original = '---\ntitle: O\n---\n\n# S\n\n"Services" {{def: services}} means x.\n'
    (tmp_path / "work").mkdir()
    _write(tmp_path / "work" / "original.lgd", original)
    document = parse(amendment)
    assert "amend-term-unresolvable" in validate(document).rules()  # no path, nothing to read
    save(document, tmp_path / "work" / "amendment.lgd")
    rules = validate(document).rules()
    assert "amend-term-unresolvable" not in rules and "amend-term-undefined" not in rules


# line endings and byte-order mark --------------------------------------------

@pytest.mark.parametrize("prefix", [b"", b"\xef\xbb\xbf"])
@pytest.mark.parametrize("ending", ["\r\n", "\r"])
def test_crlf_and_a_byte_order_mark_are_written_back_as_lf_without_one(tmp_path, prefix, ending):
    file = tmp_path / "contract.lgd"
    file.write_bytes(prefix + _SOURCE.replace("\n", ending).encode("utf-8"))
    document = load(file)
    save(document)
    data = file.read_bytes()
    assert data == _SOURCE.encode("utf-8")
    assert b"\r" not in data
    assert not data.startswith(b"\xef\xbb\xbf")
    assert load(file) == document


def test_text_is_utf_8(tmp_path):
    document = parse("---\ntitle: Smlouva o dílo\n---\n\n# Předmět\n\nZhotovitel provede dílo — „řádně“.\n")
    file = tmp_path / "cs.lgd"
    save(document, file)
    assert "Zhotovitel provede dílo — „řádně“." in file.read_bytes().decode("utf-8")


# atomicity ---------------------------------------------------------------------

def test_a_failure_while_moving_into_place_leaves_the_file_as_it_was(tmp_path, monkeypatch):
    file = _write(tmp_path / "contract.lgd")
    document = load(file)
    document.metadata.title = "Changed"

    def broken(source, destination):
        raise OSError("disk on fire")

    monkeypatch.setattr(os, "replace", broken)
    with pytest.raises(OSError, match="disk on fire"):
        save(document)
    assert file.read_text(encoding="utf-8") == _SOURCE
    assert _leftovers(tmp_path, {"contract.lgd"}) == []
    assert document.path == file.absolute()


def test_a_failure_to_save_a_new_file_leaves_no_file_and_no_path(tmp_path, monkeypatch):
    document = parse(_SOURCE)

    def broken(source, destination):
        raise OSError("disk on fire")

    monkeypatch.setattr(os, "replace", broken)
    with pytest.raises(OSError):
        save(document, tmp_path / "new.lgd")
    assert list(tmp_path.iterdir()) == []
    assert document.path is None


def test_a_document_that_cannot_be_encoded_leaves_the_file_as_it_was(tmp_path):
    file = _write(tmp_path / "contract.lgd")
    document = load(file)
    document.sections[0].title = "Lone surrogate \ud800"
    with pytest.raises(ValueError):
        save(document)
    assert file.read_text(encoding="utf-8") == _SOURCE
    assert _leftovers(tmp_path, {"contract.lgd"}) == []


def test_a_serializing_failure_writes_nothing(tmp_path, monkeypatch):
    file = _write(tmp_path / "contract.lgd")
    document = load(file)

    def broken(_document: Document) -> str:
        raise RuntimeError("cannot serialize")

    monkeypatch.setattr("legaldown.serializer.serialize", broken)
    with pytest.raises(RuntimeError):
        save(document)
    assert file.read_text(encoding="utf-8") == _SOURCE
    assert _leftovers(tmp_path, {"contract.lgd"}) == []


@pytest.mark.skipif(sys.platform == "win32", reason="symbolic links need privileges on Windows")
def test_a_symbolic_link_is_written_through(tmp_path):
    target = _write(tmp_path / "v3-final.lgd")
    link = tmp_path / "current.lgd"
    try:
        link.symlink_to(target.name)
    except OSError:
        pytest.skip("symbolic links are not available")
    document = load(link)
    document.metadata.title = "Through the link"
    assert save(document) == link.absolute()
    assert link.is_symlink()
    assert os.readlink(link) == target.name
    assert load(target).metadata.title == "Through the link"
    assert _leftovers(tmp_path, {"v3-final.lgd", "current.lgd"}) == []


@pytest.mark.skipif(sys.platform == "win32", reason="permission bits are not kept on Windows")
def test_the_permissions_of_an_existing_file_are_kept(tmp_path):
    file = _write(tmp_path / "contract.lgd")
    file.chmod(0o640)
    document = load(file)
    document.metadata.title = "Changed"
    save(document)
    assert stat.S_IMODE(file.stat().st_mode) == 0o640
    assert load(file).metadata.title == "Changed"


@pytest.mark.skipif(sys.platform == "win32", reason="permission bits are not kept on Windows")
def test_a_new_file_gets_the_permissions_a_file_made_now_would(tmp_path):
    mask = os.umask(0)
    os.umask(mask)
    save(parse(_SOURCE), tmp_path / "new.lgd")
    assert stat.S_IMODE((tmp_path / "new.lgd").stat().st_mode) == 0o666 & ~mask


# no warnings -----------------------------------------------------------------

def test_serialize_and_save_do_not_warn(tmp_path):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        save(parse(_SOURCE), tmp_path / "x.lgd")
        serialize(parse(_SOURCE))
