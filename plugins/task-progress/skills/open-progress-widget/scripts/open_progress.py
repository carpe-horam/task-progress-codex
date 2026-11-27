#!/usr/bin/env python3
"""Open a session's native, always-on-top Task Progress widget on macOS."""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import platform
from pathlib import Path
import stat
import select
import subprocess
import sys
import tempfile


def create_link(session_id=None, *, task_instance_id=None, port=53421, state_dir=None):
    path = Path(state_dir or Path.home() / '.cache/task-progress/service') / 'http-headers.json'
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or (os.name == 'posix' and
            (info.st_uid != os.getuid() or info.st_mode & 0o077)):
        raise ValueError('Service credentials must be a regular private file owned by this user.')
    authorization = json.loads(path.read_text(encoding='utf-8')).get('Authorization')
    if not isinstance(authorization, str) or not authorization.startswith('Bearer ') or len(authorization[7:]) < 32:
        raise ValueError('Invalid local service credentials.')
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=15)
    try:
        selection = {key: value for key, value in {'session_id': session_id, 'task_instance_id': task_instance_id}.items()
                     if value is not None}
        connection.request('POST', '/widget/open', json.dumps(selection),
                           {'Authorization': authorization, 'Content-Type': 'application/json',
                            'Accept': 'application/json'})
        response = connection.getresponse()
        result = json.loads(response.read())
        if response.status != 200:
            raise ValueError(result.get('error', 'Widget entry is unavailable; update the local service.'))
        return result
    finally:
        connection.close()


def widget_binary():
    if sys.platform != 'darwin':
        raise ValueError('The native widget currently supports macOS only. No browser will be opened.')
    source = Path(__file__).with_name('ProgressWidget.swift')
    digest = hashlib.sha256(source.read_bytes() + platform.machine().encode() + platform.mac_ver()[0].encode()).hexdigest()[:20]
    directory = Path.home() / '.cache/task-progress/widget' / digest
    if directory.is_symlink():
        raise ValueError('Refusing symlink widget cache.')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    binary = directory / 'TaskProgressWidget'
    if binary.is_symlink():
        raise ValueError('Refusing symlink widget executable.')
    if binary.is_file():
        return binary
    descriptor, temporary = tempfile.mkstemp(prefix='.widget-', dir=directory)
    os.close(descriptor)
    try:
        compiled = subprocess.run(['xcrun', 'swiftc', '-O', '-framework', 'AppKit', '-framework', 'WebKit',
                                   str(source), '-o', temporary], capture_output=True, text=True, timeout=120)
        if compiled.returncode:
            raise ValueError('Unable to build native widget. Install Xcode Command Line Tools.\n' + compiled.stderr)
        os.chmod(temporary, 0o700)
        os.replace(temporary, binary)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return binary


def open_widget(url):
    binary = widget_binary()
    process = subprocess.Popen([str(binary)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, start_new_session=True, text=True)
    try:
        process.stdin.write(json.dumps({'url': url})); process.stdin.close()
        ready, _, _ = select.select([process.stdout], [], [], 15)
        if not ready:
            raise ValueError('Native widget did not confirm opening within 15 seconds.')
        receipt = json.loads(process.stdout.readline())
        if receipt.get('window_opened') is not True or receipt.get('pid') != process.pid:
            raise ValueError('Native widget did not confirm its window.')
        return receipt
    except Exception:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=5)
        raise
    finally:
        process.stdout.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', help='Select a Task Progress session; omit to show the local task catalogue')
    parser.add_argument('--task-instance', help='Select one exact task instance within --session')
    parser.add_argument('--check', action='store_true', help='Check service/session access without opening a widget')
    parser.add_argument('--ensure-service', action='store_true', help='Install/update and start the bundled local service first')
    parser.add_argument('--port', type=int, default=53421, help='Loopback service port')
    parser.add_argument('--state-dir', type=Path, help='Explicit local service credential directory')
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error('port must be between 1 and 65535')
    if args.ensure_service and (args.port != 53421 or args.state_dir):
        parser.error('--ensure-service uses the default local service only')
    if args.task_instance and not args.session:
        parser.error('--task-instance requires --session')
    try:
        if args.ensure_service:
            root = Path(__file__).resolve().parents[3]
            package = next((candidate for candidate in (root, root / 'core')
                            if (candidate / 'service_manager.py').is_file()), None)
            if package is None:
                raise ValueError('This standalone Skill needs Task Progress installed first.')
            sys.path.insert(0, str(package))
            from service_manager import ServiceManager
            ServiceManager(plugin_root=package).install()
        result = create_link(args.session, task_instance_id=args.task_instance, port=args.port, state_dir=args.state_dir)
        receipt = {'selection': result['selection'], 'widget_open_requested': not args.check}
        if not args.check:
            receipt.update(open_widget(result['url']))
        print(json.dumps(receipt, ensure_ascii=False))
        return 0
    except Exception as error:
        print('Task Progress: ' + str(error) + '\nStart/update the local service with --ensure-service if needed.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
