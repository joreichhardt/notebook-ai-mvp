#!/usr/bin/env python3
"""Fetch one declared release's secrets; never render credentials or run the app."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
from urllib.parse import urlsplit
from uuid import UUID

BWS_VERSION = 'bws 2.1.0'


class ProvisionError(Exception):
    pass


def private_file(path: Path) -> str:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
            raise ProvisionError('Bootstrap file must be owned by this user and mode 0600 or stricter')
        value = stream.read(16385).strip()
    if not value or len(value) > 16384 or '\n' in value or '\r' in value:
        raise ProvisionError('Invalid bootstrap file')
    return value


def provision(manifest: dict, token_file: Path, destination: Path, bws: str = 'bws') -> None:
    url = urlsplit(manifest['server_url'])
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ProvisionError('An explicit HTTPS Bitwarden server URL is required')
    project = str(UUID(manifest['project_id']))
    entries = manifest['secrets']
    if not isinstance(entries, dict) or not entries:
        raise ProvisionError('No secrets declared')
    for name, secret_id in entries.items():
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', name):
            raise ProvisionError('Invalid output name')
        UUID(secret_id)
    parent = destination.parent
    info = parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
        raise ProvisionError('Destination parent must be an owned private directory')
    if destination.exists() or destination.is_symlink():
        raise ProvisionError('Use a new immutable release directory')
    token = private_file(token_file)
    # No inherited token, proxy, profile, config, or application environment.
    env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'LANG': 'C.UTF-8'}
    stage = Path(tempfile.mkdtemp(prefix='.secrets-', dir=parent))
    try:
        with tempfile.TemporaryDirectory(prefix='bws-home-') as home:
            env['HOME'] = home
            result = subprocess.run([bws, '--version'], env=env, capture_output=True, timeout=15)
            if result.returncode or result.stdout.decode().strip() != BWS_VERSION:
                raise ProvisionError('Unexpected bws version')
            env['BWS_ACCESS_TOKEN'] = token
            for name, secret_id in entries.items():
                result = subprocess.run(
                    [bws, '--server-url', manifest['server_url'], '--config-file',
                     str(Path(home) / 'config'), '--output', 'json', 'secret', 'get', secret_id],
                    env=env, capture_output=True, timeout=30,
                )
                if result.returncode:
                    raise ProvisionError('Secret retrieval failed')
                secret = json.loads(result.stdout)
                if secret.get('id') != secret_id or secret.get('projectId') != project:
                    raise ProvisionError('Secret identity or project mismatch')
                value = secret.get('value')
                if not isinstance(value, str) or not value.strip() or '\x00' in value or len(value) > 1048576:
                    raise ProvisionError('Invalid secret value')
                with (stage / name).open('x', encoding='utf-8') as stream:
                    os.chmod(stage / name, 0o600)
                    stream.write(value)
                    stream.flush()
                    os.fsync(stream.fileno())
            env.pop('BWS_ACCESS_TOKEN', None)
        # Same filesystem, new path. Caller serializes provisioning and rollout.
        os.rename(stage, destination)
    finally:
        env.pop('BWS_ACCESS_TOKEN', None)
        if stage.exists():
            shutil.rmtree(stage)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--token-file', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    try:
        provision(json.loads(args.manifest.read_text()), args.token_file, args.destination)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, ProvisionError):
        print('Secret provisioning failed; no new release published.', file=__import__('sys').stderr)
        return 1
    print('Secret release provisioned.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
