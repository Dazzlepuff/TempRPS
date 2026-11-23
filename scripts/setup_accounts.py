import argparse
import json
import secrets
import subprocess
import time
from pathlib import Path


def setup(installation):
    project = Path(__file__).resolve().parents[1]
    configuration = project / 'config'
    runtime_path = configuration / 'database-runtime.local.json'
    if runtime_path.exists():
        print('Accounts are already configured. Use scripts/start-accounts-db.ps1.')
        return
    candidates = [installation / 'bin' / 'mariadb-install-db.exe',
                  *installation.glob('*/bin/mariadb-install-db.exe')]
    initializer = next((path for path in candidates if path.is_file()), None)
    if initializer is None:
        raise RuntimeError('Portable MariaDB was not found. Pass --mariadb-dir with its extracted folder.')
    binary = initializer.parent
    data = project / 'data' / 'mariadb'
    if data.exists() and any(data.iterdir()):
        raise RuntimeError('Database directory already contains files; refusing to initialize over existing data.')
    configuration.mkdir(exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)
    for name in ('accounts.local.json', 'database-admin.local.ini', 'mariadb.local.ini'):
        if (configuration / name).exists():
            raise RuntimeError(f'{name} already exists; refusing to overwrite credentials.')
    root_password = secrets.token_hex(32)
    app_password = secrets.token_hex(32)

    result = subprocess.run([str(initializer), f'--datadir={data}',
                             f'--password={root_password}', '--port=3307'],
                            capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError((result.stdout + result.stderr).replace(root_password, '[redacted]'))

    server_config = configuration / 'mariadb.local.ini'
    server_config.write_text(
        '[mysqld]\n'
        f'basedir="{binary.parent.as_posix()}"\n'
        f'datadir="{data.as_posix()}"\n'
        'bind-address=127.0.0.1\nport=3307\nskip-name-resolve\n'
        'character-set-server=utf8mb4\ncollation-server=utf8mb4_unicode_ci\n'
        f'log-error="{(project / "data/accounts-db.error.log").as_posix()}"\n'
        f'pid-file="{(project / "data/accounts-db.pid").as_posix()}"\n', encoding='utf-8')
    client_config = configuration / 'database-admin.local.ini'
    client_config.write_text('[client]\nhost=127.0.0.1\nport=3307\nuser=root\n'
                             f'password={root_password}\nprotocol=tcp\n', encoding='utf-8')
    process = subprocess.Popen([str(binary / 'mariadbd.exe'), f'--defaults-file={server_config}'],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
    admin = [str(binary / 'mariadb-admin.exe'), f'--defaults-file={client_config}']
    for _ in range(80):
        if subprocess.run([*admin, 'ping', '--silent'], capture_output=True).returncode == 0:
            break
        if process.poll() is not None:
            raise RuntimeError('MariaDB exited during startup. Check data/accounts-db.error.log.')
        time.sleep(0.25)
    else:
        raise RuntimeError('MariaDB startup timed out. Check data/accounts-db.error.log.')

    sql = (project / 'database/accounts.sql').read_text(encoding='utf-8')
    sql += f"\nCREATE USER IF NOT EXISTS 'mlrps_app'@'127.0.0.1' IDENTIFIED BY '{app_password}';\n"
    sql += "GRANT SELECT, INSERT, UPDATE ON rock_paper_scissors.Account TO 'mlrps_app'@'127.0.0.1';\n"
    result = subprocess.run([str(binary / 'mariadb.exe'), f'--defaults-file={client_config}'],
                            input=sql, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr.replace(app_password, '[redacted]').replace(root_password, '[redacted]'))
    (configuration / 'accounts.local.json').write_text(json.dumps({
        'host': '127.0.0.1', 'port': 3307, 'database': 'rock_paper_scissors',
        'username': 'mlrps_app', 'password': app_password,
    }, indent=2) + '\n', encoding='utf-8')
    runtime_path.write_text(json.dumps({
        'bin': str(binary), 'server_config': str(server_config), 'client_config': str(client_config),
    }, indent=2) + '\n', encoding='utf-8')
    print('Account database ready on 127.0.0.1:3307.')
    print('Created Account table and restricted application user. Credentials saved in ignored config files.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--mariadb-dir', type=Path, default=Path.home() / 'Program Files' / 'MariaDB')
    setup(parser.parse_args().mariadb_dir)
