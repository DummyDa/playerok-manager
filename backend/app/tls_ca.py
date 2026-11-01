"""Verified CA bundle path usable by libcurl on Windows with Unicode project paths."""
import os
from pathlib import Path
import tempfile
import threading

import certifi

_lock=threading.Lock()
_copies={}
_directories=[]


def short_path(path):
    if os.name!="nt":return None
    import ctypes
    buffer=ctypes.create_unicode_buffer(32768)
    size=ctypes.windll.kernel32.GetShortPathNameW(str(path),buffer,len(buffer))
    if size and size<len(buffer) and buffer.value.isascii():return buffer.value
    return None


def ca_bundle_path():
    # Keep custom corporate/proxy trust roots instead of silently replacing them.
    source=Path(os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("CURL_CA_BUNDLE") or certifi.where()).resolve()
    try:
        data=source.read_bytes();stat=source.stat()
    except OSError:raise RuntimeError("Не найден файл сертификатов HTTPS. Повторите setup.bat или проверьте REQUESTS_CA_BUNDLE/CURL_CA_BUNDLE.") from None
    if b"-----BEGIN CERTIFICATE-----" not in data or b"-----END CERTIFICATE-----" not in data:
        raise RuntimeError("Файл сертификатов HTTPS повреждён. Повторите setup.bat.")
    if str(source).isascii():return str(source)
    shortened=short_path(source)
    if shortened and Path(shortened).is_file():return shortened
    key=(str(source),stat.st_mtime_ns,stat.st_size)
    with _lock:
        cached=_copies.get(key)
        if cached and Path(cached).is_file():return cached
        roots=[Path(tempfile.gettempdir())]
        if os.name=="nt":roots.append(Path(os.environ.get("WINDIR",r"C:\Windows"))/"Temp")
        for root in roots:
            base=str(root) if str(root).isascii() else short_path(root)
            if not base:continue
            directory=None
            try:
                directory=tempfile.TemporaryDirectory(prefix="playerok-ca-",dir=base,ignore_cleanup_errors=True)
                target=Path(directory.name)/"cacert.pem"
                if not str(target).isascii():directory.cleanup();continue
                target.write_bytes(data)
                _directories.append(directory);_copies[key]=str(target)
                return str(target)
            except OSError:
                if directory:directory.cleanup()
        raise RuntimeError("Не удалось подготовить HTTPS-сертификаты. Установите проект в папку без кириллицы, например C:\\Playerok, и повторите setup.bat.")
