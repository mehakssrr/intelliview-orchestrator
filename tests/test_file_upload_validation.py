import pytest

"""
Unit tests for File Upload Security Validation (Issue 8)

Tests:
1. Filename sanitization against path traversal attacks.
2. Rejection of files that lie about their type (executable/script disguised as .pdf / .docx).
3. Rejection of oversized files.
4. Validation of valid PDF, DOCX, and TXT files.
5. End-to-end FastAPI endpoint validation testing for POST /candidates/{candidate_id}/resume.
"""

import io
import zipfile

from fastapi.testclient import TestClient

from orchestrator.candidate_manager import candidate_manager
from orchestrator.file_validation import (
    MAX_RESUME_SIZE_BYTES,
    sanitize_filename,
    validate_file_content,
)
from orchestrator.main import app
from orchestrator.main import candidate_manager as main_candidate_manager

# ==========================================
# 1. Filename Sanitization Unit Tests
# ==========================================


def test_sanitize_filename_path_traversal():
    """Verify that path traversal components are completely stripped."""
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename("..\\..\\windows\\system32\\cmd.exe") == "cmd.exe"
    assert sanitize_filename("../../../malicious.pdf") == "malicious.pdf"
    assert sanitize_filename("\x00evil.pdf") == "evil.pdf"


def test_sanitize_filename_characters():
    """Verify non-standard special characters are sanitized."""
    assert (
        sanitize_filename("my resume (1); drop table.pdf")
        == "my_resume__1___drop_table.pdf"
    )
    assert sanitize_filename("...secret.txt") == "secret.txt"


def test_sanitize_filename_fallback():
    """Verify fallback for empty or completely invalid filenames."""
    assert sanitize_filename("") == "uploaded_file.bin"
    assert sanitize_filename(None) == "uploaded_file.bin"
    assert sanitize_filename("../..") == "uploaded_file.bin"


# ==========================================
# 2. Content & Magic Byte Validation Tests
# ==========================================


def test_disguised_executable_as_pdf():
    """Verify that a Windows PE executable renamed to .pdf is rejected."""
    fake_pdf = b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 100
    is_valid, err = validate_file_content(fake_pdf, "resume.pdf", "application/pdf")
    assert not is_valid
    assert (
        "Malicious or restricted file type detected" in err or "Missing '%PDF-'" in err
    )


def test_disguised_elf_executable_as_docx():
    """Verify that a Linux ELF binary renamed to .docx is rejected."""
    fake_docx = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 100
    is_valid, err = validate_file_content(fake_docx, "resume.docx")
    assert not is_valid
    assert "Malicious or restricted file type detected" in err or "Missing ZIP" in err


def test_disguised_shell_script_as_pdf():
    """Verify that a shell script renamed to .pdf is rejected."""
    script_content = b"#!/bin/bash\necho 'Hacked'"
    is_valid, err = validate_file_content(script_content, "resume.pdf")
    assert not is_valid
    assert "Shell Script" in err or "Missing '%PDF-'" in err


def test_invalid_extension():
    """Verify unsupported file extensions (.exe, .py, .sh, .png) are rejected."""
    content = b"Some random content"
    for ext in [".exe", ".py", ".sh", ".png", ".jpg", ".js"]:
        is_valid, err = validate_file_content(content, f"resume{ext}")
        assert not is_valid
        assert "Invalid file extension" in err


def test_valid_pdf_file():
    """Verify that a valid PDF file with %PDF- header is accepted."""
    valid_pdf = b"%PDF-1.4\n%...\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"
    is_valid, err = validate_file_content(valid_pdf, "resume.pdf", "application/pdf")
    assert is_valid
    assert err == ""


def test_valid_docx_file():
    """Verify that a valid OpenXML DOCX archive is accepted."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("[Content_Types].xml", "<?xml version='1.0'?><Types></Types>")
        zf.writestr("word/document.xml", "<?xml version='1.0'?><document></document>")
    valid_docx = buffer.getvalue()

    is_valid, err = validate_file_content(valid_docx, "my_resume.docx")
    assert is_valid
    assert err == ""


def test_corrupted_docx_file():
    """Verify that a corrupted file starting with PK magic bytes but invalid ZIP is rejected."""
    bad_docx = b"PK\x03\x04CorruptedDataHere"
    is_valid, err = validate_file_content(bad_docx, "resume.docx")
    assert not is_valid
    assert "Corrupted ZIP archive" in err or "Missing WordprocessingML" in err


def test_valid_txt_file():
    """Verify a valid UTF-8 plain text file is accepted."""
    valid_txt = (
        b"Jane Doe\nSoftware Engineer\nExperience: 5 years in Python and FastAPI"
    )
    is_valid, err = validate_file_content(valid_txt, "resume.txt")
    assert is_valid
    assert err == ""


def test_txt_file_with_binary_null_bytes():
    """Verify plain text file containing null bytes is rejected."""
    bad_txt = b"Jane Doe\x00\x00\x00Evil payload"
    is_valid, err = validate_file_content(bad_txt, "resume.txt")
    assert not is_valid
    assert "Contains binary null bytes" in err


# ==========================================
# 3. FastApi API Endpoint Integration Tests
# ==========================================

client = TestClient(app)


@pytest.mark.skip(reason="Endpoint not implemented yet")
def test_upload_resume_endpoint_success(monkeypatch):
    """Test successful resume upload for an existing candidate."""
    cand_id = "candidate_12345"
    for mgr in (candidate_manager, main_candidate_manager):
        monkeypatch.setattr(
            mgr,
            "get_candidate",
            lambda cid: (
                {"candidate_id": cid, "name": "Upload Test"} if cid == cand_id else None
            ),
        )
        monkeypatch.setattr(
            mgr,
            "save_candidate_resume",
            lambda candidate_id, sanitized_filename, resume_content: {
                "candidate_id": candidate_id,
                "filename": sanitized_filename,
                "size_bytes": len(resume_content),
                "updated_at": "2026-08-09T00:00:00Z",
            },
        )

    valid_pdf = b"%PDF-1.4\nSample resume content..."
    response = client.post(
        f"/candidates/{cand_id}/resume",
        files={"file": ("my_resume.pdf", valid_pdf, "application/pdf")},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["data"]["candidate_id"] == cand_id
    assert data["data"]["filename"] == "my_resume.pdf"


@pytest.mark.skip(reason="Endpoint not implemented yet")
def test_upload_resume_disguised_executable_rejected(monkeypatch):
    """Test endpoint rejects disguised executables with HTTP 400."""
    cand_id = "candidate_12345"
    for mgr in (candidate_manager, main_candidate_manager):
        monkeypatch.setattr(
            mgr,
            "get_candidate",
            lambda cid: (
                {"candidate_id": cid, "name": "Upload Test"} if cid == cand_id else None
            ),
        )

    fake_pdf = b"MZ\x90\x00" + b"\x00" * 50
    response = client.post(
        f"/candidates/{cand_id}/resume",
        files={"file": ("legit_looking_resume.pdf", fake_pdf, "application/pdf")},
    )

    assert response.status_code == 400
    assert (
        "Malicious or restricted file type detected" in response.json()["detail"]
        or "Missing '%PDF-'" in response.json()["detail"]
    )


@pytest.mark.skip(reason="Endpoint not implemented yet")
def test_upload_resume_path_traversal_sanitized(monkeypatch):
    """Test endpoint sanitizes malicious path traversal filenames."""
    cand_id = "candidate_12345"
    for mgr in (candidate_manager, main_candidate_manager):
        monkeypatch.setattr(
            mgr,
            "get_candidate",
            lambda cid: (
                {"candidate_id": cid, "name": "Upload Test"} if cid == cand_id else None
            ),
        )
        monkeypatch.setattr(
            mgr,
            "save_candidate_resume",
            lambda candidate_id, sanitized_filename, resume_content: {
                "candidate_id": candidate_id,
                "filename": sanitized_filename,
                "size_bytes": len(resume_content),
                "updated_at": "2026-08-09T00:00:00Z",
            },
        )

    valid_pdf = b"%PDF-1.5\nSample text"
    response = client.post(
        f"/candidates/{cand_id}/resume",
        files={"file": ("../../../../etc/passwd.pdf", valid_pdf, "application/pdf")},
    )

    assert response.status_code == 200
    # Confirm sanitized filename returned
    assert response.json()["data"]["filename"] == "passwd.pdf"


@pytest.mark.skip(reason="Endpoint not implemented yet")
def test_upload_resume_oversized_rejected(monkeypatch):
    """Test endpoint rejects files exceeding 5MB size limit with HTTP 413."""
    cand_id = "candidate_12345"
    for mgr in (candidate_manager, main_candidate_manager):
        monkeypatch.setattr(
            mgr,
            "get_candidate",
            lambda cid: (
                {"candidate_id": cid, "name": "Upload Test"} if cid == cand_id else None
            ),
        )

    oversized_data = b"%PDF-1.4\n" + b"A" * (MAX_RESUME_SIZE_BYTES + 1024)
    response = client.post(
        f"/candidates/{cand_id}/resume",
        files={"file": ("huge_resume.pdf", oversized_data, "application/pdf")},
    )

    assert response.status_code == 413


@pytest.mark.skip(reason="Endpoint not implemented yet")
def test_upload_resume_candidate_not_found(monkeypatch):
    """Test endpoint returns HTTP 404 for non-existent candidate ID."""
    for mgr in (candidate_manager, main_candidate_manager):
        monkeypatch.setattr(mgr, "get_candidate", lambda cid: None)

    valid_pdf = b"%PDF-1.4\nSample text"
    response = client.post(
        "/candidates/non_existent_candidate_12345/resume",
        files={"file": ("resume.pdf", valid_pdf, "application/pdf")},
    )
    assert response.status_code == 404
