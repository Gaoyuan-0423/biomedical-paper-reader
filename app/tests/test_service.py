"""Exercise the MyGo sidecar's real socket, private handshake and owner lifetime."""
import json
import os
import queue
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

import httpx


APP_DIR = Path(__file__).resolve().parents[1]


class SidecarServiceTests(unittest.TestCase):
    def test_private_bootstrap_real_socket_and_owner_eof(self):
        with tempfile.TemporaryDirectory(prefix='bioreader-sidecar-test-') as directory:
            root = Path(directory)
            existing = root / 'existing'
            existing.mkdir()
            marker = existing / 'do-not-touch.txt'
            marker.write_text('existing user library', encoding='utf-8')
            isolated = root / 'isolated'
            isolated.mkdir()
            env = os.environ.copy()
            env['BIOREADER_DATA_DIR'] = str(existing)
            child = subprocess.Popen([sys.executable, str(APP_DIR / 'service.py')],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8', env=env)
            try:
                token = secrets.token_hex(32)
                child.stdin.write(json.dumps({'token': token, 'diagnostic_data': str(isolated)}) + '\n')
                child.stdin.flush()
                result = queue.Queue()
                threading.Thread(target=lambda: result.put(child.stdout.readline()), daemon=True).start()
                ready = json.loads(result.get(timeout=25))
                self.assertEqual(ready['protocol'], 1)
                self.assertEqual(ready['pid'], child.pid)
                self.assertTrue(ready['url'].startswith('http://127.0.0.1:'))
                with httpx.Client(base_url=ready['url'], follow_redirects=True, trust_env=False) as client:
                    self.assertEqual(client.get('/').status_code, 403)
                    self.assertEqual(client.get('/api/papers').status_code, 403)
                    self.assertEqual(client.get('/?desktop_token=wrong').status_code, 403)
                    response = client.get('/?desktop_token=' + token)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(str(response.url), ready['url'] + '/')
                    cookie = response.history[0].headers['set-cookie']
                    self.assertIn('HttpOnly', cookie)
                    self.assertIn('SameSite=strict', cookie)
                    self.assertEqual(client.get('/api/papers').json(), [])
                    self.assertEqual(client.get('/api/papers', headers={'Origin': 'https://example.invalid'}).status_code, 403)
                    self.assertEqual(client.get('/api/papers', headers={'Sec-Fetch-Site': 'same-site'}).status_code, 403)
                    self.assertEqual(client.get('/api/papers', headers={'Host': 'localhost:1234'}).status_code, 403)
                    self.assertEqual(client.put('/api/settings', json={'model': 'sidecar-test'}).status_code, 200)
                self.assertTrue((isolated / 'settings.json').is_file())
                self.assertEqual(list(existing.iterdir()), [marker])
                self.assertEqual(marker.read_text(encoding='utf-8'), 'existing user library')
                child.stdin.close()
                child.wait(timeout=8)
                errors = child.stderr.read()
                self.assertEqual(child.returncode, 0, errors)
                self.assertNotIn(token, errors)
                with self.assertRaises(httpx.TransportError):
                    httpx.get(ready['url'], timeout=1, trust_env=False)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=5)
                for stream in (child.stdin, child.stdout, child.stderr):
                    stream.close()

    def test_owner_eof_exits_even_with_a_blocked_executor_worker(self):
        # Replace only the imported ASGI app in this child. Keep the production
        # service.run, session boundary, real TCP socket and owner-pipe watcher.
        # This avoids private data/model calls while simulating PDF/Zotero work
        # that asyncio cancellation cannot stop once it is inside a thread.
        program = r"""
import asyncio
import sys
import time
import types
from fastapi import FastAPI
sys.path.insert(0, sys.argv[1])
module = types.ModuleType('main')
module.app = FastAPI()
started = False

@module.app.get('/synthetic-blocked')
async def blocked():
    global started
    started = True
    await asyncio.to_thread(time.sleep, 30)
    return {'done': True}

@module.app.get('/synthetic-ready')
async def ready():
    return {'started': started}

sys.modules['main'] = module
import service
service.run()
"""
        with tempfile.TemporaryDirectory(prefix='bioreader-eof-worker-test-') as directory:
            child = subprocess.Popen([sys.executable, '-c', program, str(APP_DIR)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8')
            worker = None
            try:
                token = secrets.token_hex(32)
                child.stdin.write(json.dumps({'token': token, 'diagnostic_data': directory}) + '\n')
                child.stdin.flush()
                result = queue.Queue()
                threading.Thread(target=lambda: result.put(child.stdout.readline()), daemon=True).start()
                ready = json.loads(result.get(timeout=25))
                with httpx.Client(base_url=ready['url'], follow_redirects=True,
                                  trust_env=False, timeout=40) as client:
                    self.assertEqual(client.get('/?desktop_token=' + token).status_code, 404)
                    # The synthetic app deliberately has no root route; its
                    # bootstrap still sets the real session cookie before 404.
                    def request_blocked_work():
                        try:
                            client.get('/synthetic-blocked')
                        except httpx.TransportError:
                            pass  # Expected when the orphan service exits.
                    worker = threading.Thread(target=request_blocked_work, daemon=True)
                    worker.start()
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        if client.get('/synthetic-ready').json()['started']:
                            break
                        time.sleep(.02)
                    else:
                        self.fail('Synthetic executor worker did not start.')
                    began = time.monotonic()
                    child.stdin.close()
                    child.wait(timeout=8)
                    elapsed = time.monotonic() - began
                    self.assertEqual(child.returncode, 1)
                    self.assertLess(elapsed, 8)
                    worker.join(timeout=2)
                    self.assertFalse(worker.is_alive())
                self.assertNotIn(token, child.stderr.read())
                with self.assertRaises(httpx.TransportError):
                    httpx.get(ready['url'], timeout=1, trust_env=False)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=5)
                if worker:
                    worker.join(timeout=2)
                for stream in (child.stdin, child.stdout, child.stderr):
                    stream.close()

    def test_missing_owner_token_fails_before_starting_service(self):
        result = subprocess.run([sys.executable, str(APP_DIR / 'service.py')],
            input='{"token":"invalid"}\n', capture_output=True, text=True, encoding='utf-8', timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        self.assertIn('Invalid desktop owner handshake', result.stderr)


if __name__ == '__main__':
    unittest.main()
