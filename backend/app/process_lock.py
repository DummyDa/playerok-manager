from contextlib import contextmanager
import os

@contextmanager
def process_lock(path):
    path.parent.mkdir(parents=True,exist_ok=True)
    handle=path.open("a+b")
    if path.stat().st_size==0:handle.write(b"0");handle.flush()
    handle.seek(0)
    try:
        if os.name=="nt":
            import msvcrt
            msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:
        handle.close();raise RuntimeError("Процесс этого проекта уже запущен")
    try:yield
    finally:
        if os.name=="nt":
            import msvcrt
            handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
        handle.close()
