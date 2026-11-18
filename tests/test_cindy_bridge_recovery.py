import importlib.util
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

CORE = Path(__file__).resolve().parents[1] / 'plugins/task-progress/cindy/core'
if not CORE.exists():
    subprocess.run([sys.executable, str(CORE.parent / 'prepare.py')], check=True)
sys.path.insert(0, str(CORE))
spec = importlib.util.spec_from_file_location('cindy_mcp_bridge_recovery', CORE / 'mcp_bridge.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class BridgeRecoveryTest(unittest.TestCase):
    def test_failed_write_is_not_replayed_and_next_request_can_succeed(self):
        calls = []
        class Manager:
            state_dir = CORE
            def install(self):
                calls.append('install')
        class Stream:
            def __init__(self, raw): self.buffer = raw
        incoming = io.BytesIO(b'{"jsonrpc":"2.0","id":1,"method":"tools/call"}\n{"jsonrpc":"2.0","id":2,"method":"tools/call"}\n')
        outgoing = io.BytesIO()
        def forward(message, auth):
            calls.append(message['id'])
            if message['id'] == 1: raise OSError('service down')
            return {'jsonrpc':'2.0', 'id':message['id'], 'result':{'ok':True}}
        with patch.object(bridge, 'ServiceManager', Manager), patch.object(bridge, 'read_authorization', return_value='test'), patch.object(bridge, 'forward', side_effect=forward), patch.object(sys, 'stdin', Stream(incoming)), patch.object(sys, 'stdout', Stream(outgoing)):
            self.assertEqual(bridge.main(), 0)
        responses = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertEqual(calls, ['install', 1, 'install', 2])
        self.assertEqual(responses[0]['error']['code'], -32603)
        self.assertTrue(responses[1]['result']['ok'])


if __name__ == '__main__':
    unittest.main()
