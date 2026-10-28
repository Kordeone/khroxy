#!/usr/bin/env python3
"""Manage only khroxy-owned skill copies; never execute upstream hooks/installers."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manage(home, source, suite):
    home, source = Path(home), Path(source)
    codex = home / '.codex'
    skills = home / '.agents' / 'skills'
    manifest = home / '.khroxy-caveman.json'
    for path in (home, home / '.agents', skills, codex, manifest):
        if path.is_symlink():
            raise ValueError(f'Refusing symlink: {path.name}')
    old = json.loads(manifest.read_text()) if manifest.exists() else {}
    if not isinstance(old, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in old.items()):
        raise ValueError('Invalid managed skill manifest')
    desired = {}
    if suite != 'none':
        for folder in sorted((source / 'skills').iterdir()):
            if (folder / 'SKILL.md').is_file() and (suite == 'full' or folder.name in ('caveman', 'megacave', 'ultracave')):
                for path in folder.rglob('*'):
                    if path.is_symlink():
                        raise ValueError('Upstream skill contains a symlink; review required')
                    if path.is_file():
                        desired[str(path.relative_to(source / 'skills'))] = path
        if not desired:
            raise ValueError('Selected skills absent from image; rebuild with --caveman essentials or full')
    # Validate every destination before changing any managed file.
    for name in old.keys() | desired.keys():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts or len(relative.parts) < 2:
            raise ValueError('Invalid managed skill manifest')
        target = skills / relative
        for part in (target, *target.parents):
            if part == home:
                break
            if part.is_symlink():
                raise ValueError('Refusing skill symlink')
        if target.exists() and (name not in old or not target.is_file() or digest(target) != old[name]):
            raise ValueError(f'Skill conflict: {relative.parts[0]}; existing content retained. Back it up and review manually.')
    skills.mkdir(parents=True, mode=0o700, exist_ok=True)
    for name in old.keys() - desired.keys():
        (skills / name).unlink(missing_ok=True)
    for name, path in desired.items():
        target = skills / name
        target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        shutil.copyfile(path, target)
        target.chmod(0o700 if os.access(path, os.X_OK) else 0o600)
    managed_directories = set()
    for name in old.keys() - desired.keys():
        parent = (skills / name).parent
        while parent != skills:
            managed_directories.add(parent)
            parent = parent.parent
    for directory in sorted(managed_directories, key=lambda p: len(p.parts), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            pass
    data = {name: digest(path) for name, path in desired.items()}
    fd, tmp = tempfile.mkstemp(dir=home, prefix='.khroxy-caveman-')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream, indent=2)
        os.replace(tmp, manifest)
    finally:
        Path(tmp).unlink(missing_ok=True)
    print(f'Caveman skills: {suite}; {len({Path(n).parts[0] for n in desired})} skills. No SessionStart hooks installed; default mode off.')
    if (codex / 'hooks.json').exists() or (home / '.config/caveman/config.json').exists():
        print('Existing hook/config detected; retained. Review it explicitly; old defaults may still activate Caveman.', file=sys.stderr)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', choices=('none', 'essentials', 'full'), required=True)
    args = parser.parse_args()
    try:
        manage(Path.home(), Path('/opt/caveman'), args.suite)
    except (OSError, ValueError) as error:
        sys.exit(str(error))
