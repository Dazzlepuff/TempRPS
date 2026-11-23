import http.cookiejar
import json
import os
import re
import socket
import subprocess
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from uuid import uuid4


PROJECT = Path(__file__).resolve().parents[1]
PHP = Path(os.environ.get('RPS_PHP', str(Path.home() / 'Program Files/PHP/php.exe')))


class AccountTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        runtime = PROJECT / 'config/database-runtime.local.json'
        if not PHP.is_file() or not runtime.is_file():
            raise unittest.SkipTest('Portable PHP and local account database setup are required')
        cls.settings = json.loads(runtime.read_text())
        cls.client = [str(Path(cls.settings['bin']) / 'mariadb.exe'),
                      '--defaults-file=' + cls.settings['client_config'],
                      '--batch', '--skip-column-names']
        for source in [*PROJECT.joinpath('public').glob('*.php'), PROJECT / 'backend/accounts.php']:
            result = subprocess.run([str(PHP), '-l', str(source)], capture_output=True, text=True)
            if result.returncode:
                raise RuntimeError(result.stdout + result.stderr)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        cls.url = f'http://127.0.0.1:{port}'
        cls.server = subprocess.Popen([str(PHP), '-S', f'127.0.0.1:{port}',
                                       '-t', str(PROJECT / 'public')],
                                      stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        cls.addClassCleanup(cls.stop_server)
        for _ in range(50):
            try:
                urllib.request.urlopen(cls.url + '/styles.css', timeout=1).close()
                break
            except urllib.error.URLError:
                time.sleep(0.1)
        else:
            raise RuntimeError('PHP test server did not start')

    @classmethod
    def stop_server(cls):
        cls.server.terminate()
        cls.server.wait(timeout=5)

    def setUp(self):
        self.cookies = http.cookiejar.CookieJar()
        self.browser = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))
        self.username = 'test_' + uuid4().hex
        self.password = 'Test_' + uuid4().hex
        self.usernames = [self.username]
        self.addCleanup(self.remove_accounts)

    def query(self, sql):
        result = subprocess.run(self.client, input=sql, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def remove_accounts(self):
        for username in self.usernames:
            encoded = username.encode().hex()
            self.query(f'DELETE FROM rock_paper_scissors.Account WHERE Username = CONVERT(0x{encoded} USING utf8mb4);')

    def request(self, path, data=None):
        encoded = urllib.parse.urlencode(data).encode() if data is not None else None
        try:
            response = self.browser.open(self.url + path, encoded, timeout=10)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, response.read().decode(), response.url

    def token(self, page):
        status, html, _ = self.request(page)
        self.assertEqual(status, 200)
        return re.search(r'name="csrf_token" value="([a-f0-9]+)"', html).group(1)

    def register(self, username=None, password=None):
        return self.request('/user_registration_get.php', {
            'username': username or self.username, 'password': password or self.password,
            'csrf_token': self.token('/user_registration.php'),
        })

    def login(self, username=None, password=None):
        return self.request('/user_login_authenticate.php', {
            'username': username or self.username, 'password': password or self.password,
            'csrf_token': self.token('/user_login.php'),
        })

    def test_registration_login_and_password_storage(self):
        self.assertIn('Account successfully registered!', self.register()[1])
        before = next(cookie.value for cookie in self.cookies if cookie.name == 'PHPSESSID')
        status, html, url = self.login()
        self.assertEqual(status, 200)
        self.assertTrue(url.endswith('/game.php'))
        self.assertIn('Welcome ' + self.username, html)
        after = next(cookie.value for cookie in self.cookies if cookie.name == 'PHPSESSID')
        self.assertNotEqual(before, after)
        saved = self.query(f"SELECT Password FROM rock_paper_scissors.Account WHERE Username = '{self.username}';")
        self.assertNotEqual(saved, self.password)
        self.assertTrue(saved.startswith('$'))

    def test_duplicate_usernames_and_wrong_password(self):
        self.register()
        self.assertIn('Username already taken', self.register()[1])
        self.assertIn('Incorrect username or password', self.login(password='WrongPassword123')[1])
        self.assertIn('Welcome ' + self.username, self.login()[1])


    def test_html_and_quotes_are_stored_as_text_and_escaped(self):
        username = '<b>' + self.username + "'</b>"
        self.usernames.append(username)
        self.assertIn('Account successfully registered!', self.register(username=username)[1])
        html = self.login(username=username)[1]
        self.assertNotIn('<b>' + self.username, html)
        self.assertIn('&lt;b&gt;' + self.username + '&#039;&lt;/b&gt;', html)

    def test_csrf_method_and_password_validation(self):
        self.assertEqual(self.request('/user_registration_get.php')[0], 405)
        self.assertEqual(self.request('/user_login_authenticate.php')[0], 405)
        self.assertEqual(self.request('/user_registration_get.php', {'username': self.username, 'password': self.password})[0], 403)
        self.assertEqual(self.request('/user_login_authenticate.php', {'username': self.username, 'password': self.password})[0], 403)
        self.assertIn('password of 8-72 bytes', self.register(password='short')[1])
        self.assertIn('password of 8-72 bytes', self.register(password='a' * 73)[1])

    def test_access_guest_and_logout(self):
        self.assertTrue(self.request('/')[2].endswith('/user_login_menu.php'))
        for path in ('/config/accounts.local.json', '/backend/accounts.php', '/data/rps.sqlite3', '/.git/config'):
            self.assertEqual(self.request(path)[0], 404)
        self.assertTrue(self.request('/game.php')[2].endswith('/user_login_menu.php'))
        self.register()
        self.login()
        self.assertIn('Welcome Guest!', self.request('/game.php?guest=true')[1])
        session_id = next(cookie.value for cookie in self.cookies if cookie.name == 'PHPSESSID')
        session = (PHP.parent / 'sessions' / ('sess_' + session_id)).read_text()
        self.assertNotIn('accountID', session)
        self.assertNotIn('player_id', session)
        self.request('/user_login_menu.php')
        self.assertTrue(self.request('/game.php')[2].endswith('/user_login_menu.php'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
