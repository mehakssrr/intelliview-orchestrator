"""
File Upload Validation & Sanitization Module

Provides security controls for file uploads:
1. Filename sanitization (prevention of path traversal, command injection, null byte injection).
2. Streaming upload file size limits (enforces maximum allowed byte size without OOM risk).
3. Magic byte signature and content validation (detects disguised executables, extension spoofing, corrupted files).
"""

from __future__ import annotations

import logging
import os
import re
import zipfile
from io import BytesIO

from fastapi import HTTPException, UploadFile

logger = logging.getLogger(__name__)

# Default maximum upload size: 5 MB
MAX_RESUME_SIZE_BYTES = 5 * 1024 * 1024

# Allowed extensions and their corresponding MIME types
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt"}

# Executable / dangerous file signature signatures (magic bytes)
DANGEROUS_MAGIC_SIGNATURES = [
    (b"MZ", "Windows Executable (PE/DLL)"),
    (b"\x7fELF", "Linux ELF Executable"),
    (b"\xca\xfe\xba\xbe", "Java Classfile / Mach-O Fat Binary"),
    (b"\xfe\xed\xfa\xce", "Mach-O Executable (32-bit)"),
    (b"\xfe\xed\xfa\xcf", "Mach-O Executable (64-bit)"),
    (b"\xcf\xfa\xed\xfe", "Mach-O Executable (64-bit reverse)"),
    (b"#!", "Shell Script"),
    (b"<?php", "PHP Script"),
    (b"<script", "HTML Script"),
    (b"%!", "PostScript"),
]


def sanitize_filename(filename: str | None) -> str:
    """
    Sanitize an uploaded file name to prevent path traversal and injection attacks.

    - Removes path directory components (e.g. ../../etc/passwd -> passwd)
    - Replaces characters outside [a-zA-Z0-9._-] with underscores
    - Strips leading/trailing dots and spaces
    - Prevents empty or hidden filename attacks
    """
    if not filename:
        return "uploaded_file.bin"

    # Remove null bytes
    filename = filename.replace("\x00", "")

    # Extract base filename (strips any path leading up to it, Unix or Windows style)
    filename = os.path.basename(filename.replace("\\", "/"))

    # Replace any character that is not alphanumeric, dot, underscore, or hyphen
    filename = re.sub(r"[^a-zA-Z0-9._-]", "_", filename)

    # Strip leading dots (prevent hidden files / dot-dot traversal remnants)
    filename = filename.lstrip(".")

    # Ensure max filename length
    if len(filename) > 200:
        base, ext = os.path.splitext(filename)
        filename = base[: 200 - len(ext)] + ext

    if not filename:
        return "uploaded_file.bin"

    return filename


def validate_file_content(
    content: bytes, filename: str, content_type: str | None = None
) -> tuple[bool, str]:
    """
    Validate uploaded file content against its extension and content headers.

    Checks:
    1. File extension is allowed.
    2. File is non-empty.
    3. Content does NOT match known malicious magic byte signatures (PE/ELF/Scripts/etc.).
    4. Magic bytes match expected extension structure (PDF, DOCX, TXT).

    Returns:
        (is_valid: bool, error_reason: str)
    """
    if not content:
        return False, "File is empty"

    # Extract extension
    _, ext = os.path.splitext(filename.lower())
    if ext not in ALLOWED_EXTENSIONS:
        return (
            False,
            f"Invalid file extension '{ext}'. Allowed extensions: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    # Check for known dangerous magic signatures at the beginning
    header = content[:1024]
    for magic, desc in DANGEROUS_MAGIC_SIGNATURES:
        if header.startswith(magic):
            logger.warning(
                f"Blocked file upload '{filename}': matched dangerous signature '{desc}'"
            )
            return (
                False,
                f"File content security policy violation: Malicious or restricted file type detected ({desc})",
            )

    # Format specific deep validation
    if ext == ".pdf":
        # PDF magic number signature: %PDF-
        if b"%PDF-" not in content[:1024]:
            return False, "Invalid PDF file: Missing '%PDF-' header signature"

    elif ext == ".docx":
        # DOCX is an OpenXML ZIP archive starting with PK\x03\x04
        if not content.startswith(b"PK\x03\x04"):
            return False, "Invalid DOCX file: Missing ZIP/OpenXML magic signature"
        try:
            with zipfile.ZipFile(BytesIO(content)) as zf:
                # Check for standard DOCX internal component
                namelist = zf.namelist()
                if (
                    "[Content_Types].xml" not in namelist
                    and "word/document.xml" not in namelist
                ):
                    return (
                        False,
                        "Invalid DOCX file: Missing WordprocessingML document structure",
                    )
        except zipfile.BadZipFile:
            return False, "Invalid DOCX file: Corrupted ZIP archive"

    elif ext == ".txt":
        # TXT must be valid text, free of binary null bytes or unprintable shellcode
        try:
            content.decode("utf-8")
            # Check for excessive binary null bytes or control characters
            null_count = content.count(b"\x00")
            if null_count > 0:
                return False, "Invalid TXT file: Contains binary null bytes"
        except UnicodeDecodeError:
            try:
                content.decode("latin-1")
                if content.count(b"\x00") > 0:
                    return False, "Invalid TXT file: Contains binary null bytes"
            except Exception:
                return False, "Invalid TXT file: Cannot decode text content"

    return True, ""


async def validate_upload_stream(
    file: UploadFile, max_bytes: int = MAX_RESUME_SIZE_BYTES
) -> bytes:
    """
    Safely reads an uploaded file from a stream up to `max_bytes`.
    If the file exceeds `max_bytes`, raises an HTTP 413 exception immediately.
    """
    chunk_size = 64 * 1024  # 64 KB chunks
    total_bytes = 0
    chunks = []

    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        total_bytes += len(chunk)
        if total_bytes > max_bytes:
            # Consume remaining or abort to prevent uncontrolled resource exhaustion
            logger.warning(
                f"File upload size limit exceeded: {total_bytes} bytes > {max_bytes} bytes limit"
            )
            raise HTTPException(
                status_code=413,
                detail=f"Uploaded file exceeds maximum allowed size of {max_bytes // (1024 * 1024)} MB",
            )
        chunks.append(chunk)

    return b"".join(chunks)
