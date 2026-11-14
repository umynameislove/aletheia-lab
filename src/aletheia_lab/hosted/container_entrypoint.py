"""Read explicitly mounted secrets before replacing this process with API/worker."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .object_store import _regular


def secret_environment(role: str, folder: Path) -> dict[str, str]:
    names = {'database_' + role: 'ALETHEIA_HOSTED_DATABASE_URL'}
    if role == 'api':
        names.update(csrf_secret='ALETHEIA_HOSTED_CSRF_SECRET',
                     admission_secret='ALETHEIA_HOSTED_ADMISSION_SECRET',
                     login_proof_key='ALETHEIA_HOSTED_LOGIN_PROOF_KEY')
        if (folder / 'oidc_client_secret').exists():
            names['oidc_client_secret'] = 'ALETHEIA_HOSTED_OIDC_CLIENT_SECRET'
    elif role != 'worker':
        raise ValueError('Unknown hosted role.')
    values = {}
    for filename, variable in names.items():
        path = folder / filename
        _regular(path)
        with path.open('rb') as handle:
            raw = handle.read(65537)
        if len(raw) > 65536:
            raise ValueError('Invalid mounted secret.')
        value = raw.decode().strip()
        if not value or '\n' in value or '\r' in value or '\x00' in value:
            raise ValueError('Invalid mounted secret.')
        values[variable] = value
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('role', choices=('api', 'worker'))
    parser.add_argument('--secret-dir', type=Path, default=Path('/run/secrets'))
    parser.add_argument('--config-dir', type=Path, default=Path('/run/config'))
    args = parser.parse_args()
    try:
        values = secret_environment(args.role, args.secret_dir)
        values.update(ALETHEIA_HOSTED_CONFIG=str(args.config_dir / (args.role + '.toml')),
                      ALETHEIA_HOSTED_AUTH_POLICY=str(args.config_dir / 'auth.toml'),
                      ALETHEIA_HOSTED_ACTION_AUDIT='enabled')
        if (args.config_dir / 'idp-ca.pem').is_file():
            values['ALETHEIA_HOSTED_IDP_CA_FILE'] = str(args.config_dir / 'idp-ca.pem')
        command = [sys.executable, '-m', 'aletheia_lab.hosted.managed_worker']
        if args.role == 'api':
            command = [sys.executable, '-m', 'aletheia_lab.hosted.server',
                       '--config', values['ALETHEIA_HOSTED_CONFIG'],
                       '--auth-policy', values['ALETHEIA_HOSTED_AUTH_POLICY'],
                       '--tls-cert', str(args.secret_dir / 'tls_cert'),
                       '--tls-key', str(args.secret_dir / 'tls_key')]
        os.execve(sys.executable, command, {**os.environ, **values})
    except Exception:
        print('Hosted container configuration is unavailable.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
