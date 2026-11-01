from __future__ import annotations

import base64
import ctypes
import sys

_IS_WINDOWS = sys.platform == "win32"
_PREFIX = "dpapi:"


def available() -> bool:
    return _IS_WINDOWS


def _crypt(data: bytes, protect: bool) -> bytes:
    if not _IS_WINDOWS:
        raise OSError("DPAPI is only available on Windows")
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    buffer = ctypes.create_string_buffer(data, len(data))
    in_blob = DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    out_blob = DATA_BLOB()
    if protect:
        ok = crypt32.CryptProtectData(
            ctypes.byref(in_blob), "PyMCLauncher account", None,
            None, None, 0x1, ctypes.byref(out_blob))
    else:
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(in_blob), None, None, None, None,
            0x1, ctypes.byref(out_blob))
    if not ok:
        raise OSError(ctypes.get_last_error(), "DPAPI operation failed")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def protect_text(text: str) -> str:
    raw = _crypt(text.encode("utf-8"), True)
    return _PREFIX + base64.b64encode(raw).decode("ascii")


def unprotect_text(value: str) -> str:
    if not value.startswith(_PREFIX):
        raise ValueError("unsupported protected value")
    raw = base64.b64decode(value[len(_PREFIX):])
    return _crypt(raw, False).decode("utf-8")
