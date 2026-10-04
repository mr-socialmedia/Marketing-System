"""
Encryption for the one sensitive field this agent stores: the full path.

WHY A PATH IS SENSITIVE AT ALL.

`D:\\Work\\Platinum\\Legal\\termination-draft-final.docx` is not a filename, it
is a disclosure. The agent needs the path to work out the project and to offer
"open this file" later, but it has no reason to keep it in a form that anyone
reading the database file can use. So the path is encrypted at rest and the
derived, non-sensitive parts — extension, project, category, a stem that the
sanitizer has already blunted — are stored in the clear, because those are what
every query actually filters on.

DPAPI FIRST, FERNET SECOND.

On Windows the ciphertext is produced by CryptProtectData, which ties it to the
logged-in Windows account. That is the right primitive here: it means another
user on the same machine cannot decrypt the database even with the file in
hand, and there is no key file to lose, leak or back up by accident. It is
reached through ctypes rather than pywin32 so it costs no dependency.

Fernet is the fallback, for tests and for anywhere that is not Windows. Its key
lives in a file with the most restrictive permissions the platform offers, and
that is genuinely weaker — anyone who can read the database can probably read
the key beside it. The class says so out loud rather than letting the word
"encrypted" imply more than it delivers.
"""

from __future__ import annotations

import base64
import ctypes
import logging
import os
import platform
import secrets
from ctypes import wintypes
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)

# A short prefix so a stored value declares which scheme produced it. Without
# it, rotating schemes means guessing, and guessing wrong means a decrypt that
# returns plausible garbage instead of an error.
_DPAPI_PREFIX = b"DPAPI1:"
_FERNET_PREFIX = b"FERN1:"


class EncryptionError(RuntimeError):
    """Raised when a value cannot be protected or recovered."""


class Cipher(Protocol):
    """The two operations anything storing a path needs."""

    scheme: str

    def encrypt(self, plaintext: str) -> bytes: ...
    def decrypt(self, blob: bytes) -> str: ...


# ======================================================================
# Windows DPAPI
# ======================================================================
class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob_to_bytes(blob: _DataBlob) -> bytes:
    return ctypes.string_at(blob.pbData, blob.cbData)


class DpapiCipher:
    """
    CryptProtectData / CryptUnprotectData, scoped to the current Windows user.

    CRYPTPROTECT_UI_FORBIDDEN is passed deliberately: without it, a protected
    value whose key needs a prompt will pop a dialog from a background thread,
    which on a tracker running at login is an unexplained window appearing over
    whatever the person is doing.

    The entropy argument is a fixed application salt, not a secret. It stops a
    different program running as the same user from decrypting these blobs by
    accident; it is not pretending to be a second factor.
    """

    scheme = "dpapi"
    _ENTROPY = b"mohamed-work-tracker/v1/path"

    def __init__(self) -> None:
        if platform.system() != "Windows":
            raise EncryptionError("DPAPI is only available on Windows")
        self._crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined]
        self._kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

    def _entropy_blob(self) -> _DataBlob:
        buf = ctypes.create_string_buffer(self._ENTROPY, len(self._ENTROPY))
        return _DataBlob(len(self._ENTROPY), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

    def encrypt(self, plaintext: str) -> bytes:
        data = plaintext.encode("utf-8")
        src = ctypes.create_string_buffer(data, len(data))
        blob_in = _DataBlob(len(data), ctypes.cast(src, ctypes.POINTER(ctypes.c_char)))
        entropy = self._entropy_blob()
        blob_out = _DataBlob()

        CRYPTPROTECT_UI_FORBIDDEN = 0x01
        ok = self._crypt32.CryptProtectData(
            ctypes.byref(blob_in), None, ctypes.byref(entropy),
            None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out),
        )
        if not ok:
            raise EncryptionError(f"CryptProtectData failed (error {self._kernel32.GetLastError()})")
        try:
            return _DPAPI_PREFIX + _blob_to_bytes(blob_out)
        finally:
            # The API allocates with LocalAlloc; not freeing it leaks a little
            # memory on every single tracked event, which over a working day is
            # not a little.
            self._kernel32.LocalFree(blob_out.pbData)

    def decrypt(self, blob: bytes) -> str:
        if not blob.startswith(_DPAPI_PREFIX):
            raise EncryptionError("Not a DPAPI value")
        payload = blob[len(_DPAPI_PREFIX):]
        src = ctypes.create_string_buffer(payload, len(payload))
        blob_in = _DataBlob(len(payload), ctypes.cast(src, ctypes.POINTER(ctypes.c_char)))
        entropy = self._entropy_blob()
        blob_out = _DataBlob()

        CRYPTPROTECT_UI_FORBIDDEN = 0x01
        ok = self._crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, ctypes.byref(entropy),
            None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out),
        )
        if not ok:
            raise EncryptionError(f"CryptUnprotectData failed (error {self._kernel32.GetLastError()})")
        try:
            return _blob_to_bytes(blob_out).decode("utf-8")
        finally:
            self._kernel32.LocalFree(blob_out.pbData)


# ======================================================================
# Fernet fallback
# ======================================================================
class FernetCipher:
    """
    Symmetric encryption with a local key file.

    WEAKER THAN DPAPI, ON PURPOSE EXPLICIT. The key sits next to the database,
    so this protects against a stolen database file and nothing else — not
    against another process running as the same user, and not against someone
    who copies the whole folder. It exists so the tests run anywhere and so a
    non-Windows machine degrades to something rather than to plaintext.
    """

    scheme = "fernet"

    def __init__(self, key_path: Path) -> None:
        try:
            from cryptography.fernet import Fernet
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise EncryptionError("cryptography is required for the Fernet fallback") from exc

        self._key_path = Path(key_path)
        self._Fernet = Fernet
        self._fernet = Fernet(self._load_or_create_key())

    def _load_or_create_key(self) -> bytes:
        if self._key_path.exists():
            key = self._key_path.read_bytes().strip()
            if key:
                return key
            log.warning("Key file %s was empty; generating a new key.", self._key_path)

        key = base64.urlsafe_b64encode(secrets.token_bytes(32))
        self._key_path.parent.mkdir(parents=True, exist_ok=True)

        # Create with owner-only permissions FROM THE START. Writing the file
        # and then chmod'ing it leaves a window in which the key is world
        # readable, and that window is exactly when a backup agent runs.
        fd = os.open(str(self._key_path), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        try:
            os.write(fd, key)
        finally:
            os.close(fd)

        try:
            os.chmod(self._key_path, 0o600)
        except OSError:
            # Windows ignores POSIX modes. The file still inherits the user
            # profile's ACL, which is why DPAPI is preferred there.
            pass
        return key

    def encrypt(self, plaintext: str) -> bytes:
        return _FERNET_PREFIX + self._fernet.encrypt(plaintext.encode("utf-8"))

    def decrypt(self, blob: bytes) -> str:
        if not blob.startswith(_FERNET_PREFIX):
            raise EncryptionError("Not a Fernet value")
        try:
            return self._fernet.decrypt(blob[len(_FERNET_PREFIX):]).decode("utf-8")
        except Exception as exc:
            raise EncryptionError("Could not decrypt value") from exc


# ======================================================================
# Selection
# ======================================================================
def build_cipher(key_path: Path | str, prefer_dpapi: bool = True) -> Cipher:
    """
    The best available scheme, announced in the log.

    A silent downgrade from DPAPI to a key file is the kind of thing nobody
    notices until it matters, so it is logged at WARNING and the chosen scheme
    is readable from `cipher.scheme` for the UI to show.
    """
    if prefer_dpapi and platform.system() == "Windows":
        try:
            cipher = DpapiCipher()
            # Prove it works now rather than at the first tracked event, when
            # the failure would surface as a lost day of activity.
            if cipher.decrypt(cipher.encrypt("self-test")) != "self-test":
                raise EncryptionError("DPAPI round-trip did not match")
            log.info("Path encryption: Windows DPAPI (bound to this Windows account).")
            return cipher
        except EncryptionError as exc:
            log.warning("DPAPI unavailable (%s); falling back to a local key file.", exc)

    log.warning(
        "Path encryption: Fernet with a local key at %s. This is weaker than "
        "DPAPI — anyone who can read the database can probably read the key.",
        key_path,
    )
    return FernetCipher(Path(key_path))


def decrypt_any(blob: bytes, ciphers: list[Cipher]) -> str:
    """
    Decrypt a value written by whichever scheme was in use at the time.

    Rows written before a machine change still have to open, so the reader
    tries every scheme it has rather than assuming the current one.
    """
    last: Exception | None = None
    for cipher in ciphers:
        try:
            return cipher.decrypt(blob)
        except EncryptionError as exc:
            last = exc
    raise EncryptionError("No configured cipher could read this value") from last
