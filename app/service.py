"""Bundled backend sidecar. Its owner keeps stdin open for the service lifetime."""
from __future__ import annotations

import json
import os
import re
import socket
import sys
import threading
import time
from pathlib import Path

from desktop_paths import desktop_data_dir
from service_security import protect_service


def run():
    # The first line is a private owner-to-child handshake. Tokens never occur
    # in command-line arguments, environment variables or application logs.
    control = sys.stdout
    sys.stdout = sys.stderr
    line = sys.stdin.buffer.readline(8193)
    if not line.endswith(b'\n') or len(line) > 8192:
        raise RuntimeError('Missing desktop owner handshake.')
    configuration = json.loads(line)
    token = configuration.get('token', '')
    if not isinstance(token, str) or not re.fullmatch(r'[a-f0-9]{64}', token):
        raise RuntimeError('Invalid desktop owner handshake.')
    diagnostic_data = configuration.get('diagnostic_data')
    if diagnostic_data is not None:
        path = Path(diagnostic_data).resolve()
        if not path.is_dir() or any(path.iterdir()):
            raise RuntimeError('Diagnostic storage must be an empty temporary directory.')
        os.environ['BIOREADER_DATA_DIR'] = str(path)
    else:
        os.environ['BIOREADER_DATA_DIR'] = str(desktop_data_dir())
    sys.path.insert(0, str(Path(__file__).resolve().parent / 'backend'))
    import uvicorn
    from main import app

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(('127.0.0.1', 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    protect_service(app, port, token)
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port,
        log_level='warning', access_log=False, use_colors=False, timeout_graceful_shutdown=3))

    def watch_owner():
        # Closing the pipe also covers an abrupt parent crash, on both OSes.
        try:
            while sys.stdin.buffer.read(4096):
                pass
        finally:
            server.should_exit = True
            # The owner may have crashed while a PDF/native worker is blocked.
            # Graceful shutdown gets a bounded opportunity before this orphan
            # process exits independently of its now-missing parent.
            deadline = threading.Timer(5, lambda: os._exit(1))
            deadline.daemon = True
            deadline.start()

    def ready():
        while not server.started and not server.should_exit:
            time.sleep(.02)
        if server.started and not server.should_exit:
            control.write(json.dumps({'protocol': 1, 'url': f'http://127.0.0.1:{port}',
                'pid': os.getpid(), 'frozen': bool(getattr(sys, 'frozen', False))}) + '\n')
            control.flush()

    threading.Thread(target=watch_owner, daemon=True).start()
    threading.Thread(target=ready, daemon=True).start()
    try:
        server.run(sockets=[listener])
    finally:
        listener.close()


if __name__ == '__main__':
    run()
