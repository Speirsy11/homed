"""Private local database file creation, shared by dashboard stores."""
import os
from pathlib import Path


def private_database(path):
    path = Path(path)
    if not path.parent.exists():
        path.parent.mkdir(parents=True, mode=0o700)
    if path.is_symlink():
        raise ValueError('Refusing a symlink as a dashboard database')
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    os.fchmod(fd, 0o600)
    os.close(fd)
    return path

