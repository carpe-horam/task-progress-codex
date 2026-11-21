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
            def health(self):
                return None
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
        with patch.object(bridge, '_package_version', return_value='0.2.57'), patch.object(bridge, 'ServiceManager', Manager), patch.object(bridge, 'read_authorization', return_value='test'), patch.object(bridge, 'forward', side_effect=forward), patch.object(sys, 'stdin', Stream(incoming)), patch.object(sys, 'stdout', Stream(outgoing)):
            self.assertEqual(bridge.main(), 0)
        responses = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertEqual(calls, ['install', 1, 'install', 2])
        self.assertEqual(responses[0]['error']['code'], -32603)
        self.assertTrue(responses[1]['result']['ok'])

    def run_with_health(self, version, local_version='0.2.57'):
        calls=[]
        class Manager:
            state_dir=CORE
            def health(self):
                return {'service':'task-progress','transport':'streamable-http','port':53421,
                        'pid':12,'instance_id':'fixture','runtime_path':'/fixture/shared_service.py',
                        'version':version}
            def install(self): calls.append('install')
        class Stream:
            def __init__(self, raw): self.buffer=raw
        incoming=io.BytesIO(b'{"jsonrpc":"2.0","id":1,"method":"tools/call"}\n')
        outgoing=io.BytesIO()
        with patch.object(bridge,'_package_version',return_value=local_version), patch.object(bridge,'ServiceManager',Manager), patch.object(bridge,'read_authorization',return_value='test'), patch.object(bridge,'forward',return_value={'jsonrpc':'2.0','id':1,'result':{'ok':True}}), patch.object(sys,'stdin',Stream(incoming)), patch.object(sys,'stdout',Stream(outgoing)):
            self.assertEqual(bridge.main(),0)
        return calls, [json.loads(line) for line in outgoing.getvalue().splitlines()]

    def test_newer_same_protocol_daemon_is_reused_without_startup_install(self):
        calls,responses=self.run_with_health('0.2.57','0.2.30')
        self.assertEqual(calls,[])
        self.assertTrue(responses[0]['result']['ok'])

    def test_equal_version_still_calls_install_to_reconcile_a_new_digest(self):
        calls,_=self.run_with_health('0.2.57','0.2.57')
        self.assertEqual(calls,['install'])

    def test_incompatible_protocol_is_not_reused(self):
        calls,_=self.run_with_health('0.3.0','0.2.57')
        self.assertEqual(calls,['install'])


if __name__ == '__main__':
    unittest.main()
