#!/usr/bin/env python3
"""Validate read-only image / writable mounts without touching project contents."""
import argparse
import os
from pathlib import Path
import pwd
import re
import tempfile


WRITABLE_MOUNTS = ('/home/khroxy/.config/khroxy', '/home/agent', '/run', '/tmp', '/workspace')


def mounts(text):
    result = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 7 or ' - ' not in line:
            raise ValueError('invalid mount metadata')
        path = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), fields[4])
        result[path] = set(fields[5].split(','))
    return result


def check_layout(text):
    layout = mounts(text)
    if 'ro' not in layout.get('/', set()):
        raise ValueError('image root must be read-only')
    for path in WRITABLE_MOUNTS:
        if 'rw' not in layout.get(path, set()):
            raise ValueError(f'missing writable mount: {path}')


def check_writable(path):
    """Create/remove only a new private probe file, never chmod existing data."""
    if path.is_symlink() or not path.is_dir():
        raise ValueError('runtime directory missing or symlinked')
    fd, name = tempfile.mkstemp(prefix='.khroxy-write-check-', dir=path)
    try:
        os.write(fd, b'khroxy runtime check\n')
        os.fsync(fd)
    finally:
        os.close(fd)
        os.unlink(name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('role', choices=('khroxy', 'agent'))
    args = parser.parse_args()
    try:
        user = pwd.getpwnam(args.role)
        if (os.geteuid(), os.getegid()) != (user.pw_uid, user.pw_gid):
            raise ValueError('runtime check must run as the owning UID/GID')
        check_layout(Path('/proc/self/mountinfo').read_text())
        path = Path('/home/khroxy/.config/khroxy' if args.role == 'khroxy' else '/home/agent')
        check_writable(path)
        check_writable(Path('/tmp'))
        if args.role == 'agent' and not os.access('/workspace', os.R_OK | os.W_OK | os.X_OK, effective_ids=True):
            raise ValueError('project mount inaccessible to agent UID/GID')
        print(f'khroxy-startup: writable runtime verified role={args.role} uid={os.geteuid()} gid={os.getegid()}')
    except (OSError, ValueError, KeyError) as error:
        # Raw exceptions can contain filenames/credentials from persisted data.
        code = f' errno={error.errno}' if isinstance(error, OSError) else ''
        reason = f' reason={error}' if type(error) is ValueError else ''
        print(f'khroxy-startup: runtime validation failed role={args.role} kind={type(error).__name__}{code}{reason}', flush=True)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
