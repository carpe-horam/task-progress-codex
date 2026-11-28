"""Real HTTP/process regressions; all progress and daemon state are temporary."""
import concurrent.futures
import http.client
import importlib.util
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

PLUGIN = Path(__file__).resolve().parents[1] / "plugins/task-progress"


class CoreStorageIsolationTests(unittest.TestCase):
    def test_widget_tokens_expire_without_exposing_native_credentials(self):
        spec = importlib.util.spec_from_file_location('browser_expiry_service', PLUGIN / 'shared_service.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            core = module.load_core(Path(temporary) / 'sessions')
            task = core.init_task_network(task_title='Expiring browser link', atomic_reason='One operation')['structuredContent']
            with module.SharedMCPServer(0, 'Bearer native-service-secret', core) as service:
                with patch.object(module.time, 'time', return_value=100):
                    link = service.widget_link(task['session_id'])
                    token = 'Bearer ' + link['url'].split('#')[1]
                    self.assertEqual(service.widget_session(token)['selection']['session_id'], task['session_id'])
                with patch.object(module.time, 'time', return_value=link['expires_at']):
                    self.assertIsNone(service.widget_session(token))
                    self.assertNotEqual(service.widget_link(task['session_id'])['url'], link['url'])
                self.assertIsNone(service.widget_session('Bearer native-service-secret'))

    def test_explicit_session_directory_never_probes_default_legacy_store(self):
        spec = importlib.util.spec_from_file_location("isolated_shared_service", PLUGIN / "shared_service.py")
        service = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(service)
        with tempfile.TemporaryDirectory(prefix="task-progress-core-isolation-") as temporary:
            root = Path(temporary)
            fake_home = root / "home"
            default_db = fake_home / ".cache/task-progress/progress-v3.sqlite3"
            default_db.parent.mkdir(parents=True)
            default_db.write_bytes(b"unrelated default-store sentinel")
            isolated_sessions = root / "isolated/sessions"
            sid = "00000000-0000-4000-8000-000000000053"
            with patch.object(Path, "home", return_value=fake_home):
                core = service.load_core(isolated_sessions)
            self.assertEqual(core.LEGACY_DB_PATH, isolated_sessions.parent / "progress-v3.sqlite3")
            # A broken default archive must not appear as an isolation warning.
            self.assertIsNone(core.get_progress_view(sid)["structuredContent"]["progress"])
            self.assertEqual(default_db.read_bytes(), b"unrelated default-store sentinel")
            # The explicitly selected sibling archive is the only legacy source.
            core.LEGACY_DB_PATH.write_bytes(b"isolated broken archive")
            archive = core.get_progress_view(sid)["structuredContent"]["progress"]
            self.assertEqual(archive["mode"], "legacy_archive")
            self.assertEqual(archive["legacy_history_warning"], "legacy_history_unavailable")
            self.assertEqual(core.LEGACY_DB_PATH.read_bytes(), b"isolated broken archive")
            with patch.object(Path, "home", return_value=fake_home):
                production = service.load_core()
            self.assertEqual(production.LEGACY_DB_PATH, default_db)


class SharedServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="task-progress-http-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "service"
        self.state.mkdir(mode=0o700)
        self.authorization = "Bearer " + secrets.token_urlsafe(32)
        credentials = self.state / "http-headers.json"
        credentials.write_text(json.dumps({"Authorization": self.authorization}))
        credentials.chmod(0o600)
        self.proc = None
        self.addCleanup(self.stop)
        self.start()

    def command(self, *, state=None, port=0):
        return [sys.executable, "-B", str(PLUGIN / "shared_service.py"), "--port", str(port),
                "--state-dir", str(state or self.state), "--session-dir", str(self.root / "sessions")]

    def start(self, *, port=0):
        self.proc = subprocess.Popen(self.command(port=port), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        until = time.monotonic() + 5
        while time.monotonic() < until:
            if self.proc.poll() is not None:
                self.fail("Service exited: " + self.proc.stderr.read())
            path = self.state / "daemon.json"
            if path.exists():
                metadata = json.loads(path.read_text())
                # A crash leaves the predecessor's metadata until the new
                # process acquires its lock and binds the listening socket.
                if metadata["pid"] == self.proc.pid:
                    self.metadata = metadata
                    self.port = metadata["port"]
                    return
            time.sleep(0.02)
        self.fail("Service startup timed out")

    def stop(self):
        if self.proc is not None:
            if self.proc.poll() is None:
                self.proc.terminate()
            try:
                self.proc.communicate(timeout=4)
            except subprocess.TimeoutExpired:
                self.proc.kill(); self.proc.communicate()
                raise AssertionError("Shared service did not shut down normally")
            self.proc = None

    def http(self, method="POST", path="/mcp", payload=None, *, headers=None, raw=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=4)
        base = {"Authorization": self.authorization, "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}
        base.update(headers or {})
        try:
            body = raw if raw is not None else json.dumps(payload) if payload is not None else None
            connection.request(method, path, body=body, headers=base)
            response = connection.getresponse(); data = response.read()
            return response.status, dict(response.getheaders()), json.loads(data) if data else None
        finally:
            connection.close()

    def rpc(self, name, arguments, *, request_id=1):
        status, headers, message = self.http(payload={"jsonrpc": "2.0", "id": request_id,
            "method": "tools/call", "params": {"name": name, "arguments": arguments}})
        self.assertEqual(status, 200, message)
        self.assertEqual(message["id"], request_id)
        self.assertNotIn("error", message)
        self.assertFalse(message["result"].get("isError"), message)
        self.assertEqual(int(headers["X-Task-Progress-Pid"]), self.proc.pid)
        return message["result"]["structuredContent"]

    def test_host_archive_is_persistent_across_daemon_restart(self):
        task = self.rpc('init_task_network', {'task_title': 'Host archived goal', 'atomic_reason': 'One fixture'})
        sid = task['session_id']
        before = (self.root / 'sessions' / (sid + '.json')).read_bytes()
        request = {'jsonrpc': '2.0', 'id': 1, 'method': 'task-progress/archive-host-session',
                   'params': {'session_id': sid, 'host_session_id': 'host-http', 'source': 'cindy'}}
        status, _, response = self.http(payload=request)
        self.assertEqual(status, 200)
        self.assertTrue(response['result']['changed'])
        self.stop()
        self.start()
        status, _, response = self.http(payload=request)
        self.assertEqual(status, 200)
        self.assertFalse(response['result']['changed'])
        self.assertEqual((self.root / 'sessions' / (sid + '.json')).read_bytes(), before)
        self.assertEqual(self.rpc('get_progress', {'session_id': sid})['progress']['status'], 'in_progress')

    def test_multiple_clients_share_one_pid_without_mixing_request_ids_or_sessions(self):
        def create(index):
            # Independent clients deliberately reuse JSON-RPC ID 1.
            result = self.rpc("init_task_network", {"task_title": f"Client {index}",
                "atomic_reason": "One operation", "request_id": f"client-{index}"})
            sid = result["session_id"]; instance = result["task_instance_id"]
            run = self.rpc("update_task", {"session_id": sid, "task_instance_id": instance,
                "task_id": "root", "execution_status": "running", "assignment": {"execution_mode": "main_session"}})
            attempt = run["attempt_id"]
            self.rpc("update_task", {"session_id": sid, "task_instance_id": instance, "task_id": "root",
                "execution_status": "finished", "outcome": "resolved", "attempt_id": attempt,
                "summary": f"Result {index}"})
            final = self.rpc("get_progress", {"session_id": sid, "view": "full"})["progress"]
            self.assertEqual(final["task_title"], f"Client {index}")
            self.assertEqual(final["nodes"][0]["summary"], f"Result {index}")
            return sid
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            sessions = list(pool.map(create, range(12)))
        self.assertEqual(len(set(sessions)), 12)
        status, _, health = self.http("GET", "/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(health["pid"], self.proc.pid)
        self.assertEqual(health["request_count"], 48)

    def test_concurrent_repeated_init_is_idempotent(self):
        def initialize(_):
            return self.rpc("init_task_network", {"task_title": "One shared request", "request_id": "one-key",
                        "atomic_reason": "One operation"})
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(initialize, range(8)))
        self.assertEqual(len({r["session_id"] for r in results}), 1)
        self.assertEqual(len({r["task_instance_id"] for r in results}), 1)

    def test_widget_catalogue_is_read_only_and_cli_never_exposes_credentials(self):
        first = self.rpc('init_task_network', {'task_title': 'Browser A', 'atomic_reason': 'One operation'})
        second = self.rpc('init_task_network', {'task_title': 'Browser B', 'atomic_reason': 'One operation'})
        status, _, link = self.http(path='/widget/open', payload={'session_id': first['session_id']})
        self.assertEqual(status, 200, link)
        token = link['url'].split('#')[1]
        self.assertNotIn(token, self.authorization)
        headers = {'Authorization': 'Bearer ' + token, 'Origin': f'http://127.0.0.1:{self.port}'}
        self.assertEqual(self.http('GET', '/widget/session', headers=headers)[2]['selection']['session_id'], first['session_id'])
        catalogue = self.http('GET', '/widget/catalogue', headers=headers)[2]
        self.assertEqual({item['session_id'] for item in catalogue['tasks']}, {first['session_id'], second['session_id']})
        def read(sid, name='get_progress_view'):
            return self.http(path='/widget/rpc', headers=headers, payload={'jsonrpc': '2.0', 'id': 7,
                'method': 'tools/call', 'params': {'name': name, 'arguments': {'session_id': sid}}})[2]
        self.assertEqual(read(first['session_id'])['result']['structuredContent']['progress']['task_title'], 'Browser A')
        self.assertEqual(read(second['session_id'])['result']['structuredContent']['progress']['task_title'], 'Browser B')
        self.assertIn('error', read('00000000-0000-4000-8000-000000000099'))
        self.assertIn('error', read(first['session_id'], 'clear_progress'))
        self.assertIn('error', read(first['session_id'], 'register_ui_view'))
        self.assertEqual(self.http('GET', '/healthz', headers=headers)[0], 403)  # Origin still forbidden for native endpoints.
        self.assertEqual(self.http('GET', '/widget/session', headers={'Authorization': 'Bearer invalid'})[0], 401)
        hostile = {**headers, 'Origin': 'https://untrusted.example'}
        self.assertEqual(self.http('GET', '/widget/session', headers=hostile)[0], 403)
        self.assertEqual(self.http('GET', '/widget/session', headers={**headers, 'Host': 'untrusted.example'})[0], 403)
        self.assertEqual(self.http(path='/widget/open', payload={'session_id': 'missing'})[0], 400)
        command = [sys.executable, str(PLUGIN / 'progress_widget.py'), '--session', first['session_id'],
                   '--port', str(self.port), '--state-dir', str(self.state), '--source', 'unknown', '--check']
        result = subprocess.run(command, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt['selection']['session_id'], first['session_id'])
        self.assertFalse(receipt['widget_open_requested'])
        self.assertNotIn('url', receipt)
        self.assertNotIn(self.authorization, result.stdout + result.stderr)
        self.stop(); self.start()
        self.assertEqual(self.http('GET', '/widget/session', headers={'Authorization': 'Bearer ' + token})[0], 401)
    def test_http_tools_dispatch_submit_review_and_main_only_synthesis(self):
        main = {"execution_mode": "main_session", "self_execution_reason": "Coordinator checks one integration decision"}
        sub = {"execution_mode": "subagent", "model": "fixture", "reasoning_effort": "high",
               "tool_identifier": "collaboration.spawn_agent", "dispatch_text": "Run isolated fixture and return evidence"}
        init = self.rpc("init_task_network", {"task_title": "HTTP responsibility contract", "tasks": [
            {"id": "worker", "title": "Delegated operation", "assignment": sub},
            {"id": "coord", "title": "Coordinator operation", "assignment": main}]})
        base = {"session_id": init["session_id"], "task_instance_id": init["task_instance_id"]}
        self.rpc("reassign_task", {**base, "task_id": "coord", "assignment": main,
                                   "reason": "Confirm bounded integration responsibility", "reviewer": main})
        started = self.rpc("update_task", {**base, "task_id": "worker", "execution_status": "running", "assignment": sub})
        aid = started["attempt_id"]
        self.rpc("bind_task_agent", {**base, "task_id": "worker", "attempt_id": aid, "agent_name": "http-fixture-agent"})
        submitted = self.rpc("update_task", {**base, "task_id": "worker", "attempt_id": aid,
            "execution_status": "finished", "outcome": "resolved", "summary": "Worker output", "evidence": ["fixture://worker"]})
        node = submitted["affected"]
        self.assertEqual(node["review_status"], "pending")
        review = {**base, "task_id": "worker", "attempt_id": aid, "decision": "accept",
                  "summary": "Checked independently", "evidence": ["fixture://review"], "reviewer": sub}
        status, _, result = self.http(payload={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "review_task", "arguments": review}})
        self.assertEqual(status, 200)
        self.assertTrue(result["result"]["isError"])
        self.rpc("review_task", {**review, "reviewer": main})
        started = self.rpc("update_task", {**base, "task_id": "coord", "execution_status": "running", "assignment": main})
        coord_aid = started["attempt_id"]
        self.rpc("update_task", {**base, "task_id": "coord", "attempt_id": coord_aid,
                                 "execution_status": "finished", "outcome": "resolved"})
        synthesis = {**base, "task_id": "root", "execution_status": "finished", "outcome": "resolved",
                     "assignment": sub, "summary": "Synthesis", "evidence": ["fixture://all"]}
        status, _, result = self.http(payload={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "update_task", "arguments": synthesis}})
        self.assertEqual(status, 200)
        self.assertTrue(result["result"]["isError"])
        finished = self.rpc("update_task", {**synthesis, "assignment": main})
        self.assertEqual(finished["status"], "completed")

    def test_streamable_handshake_notifications_and_resource_version(self):
        status, headers, message = self.http(payload={"jsonrpc": "2.0", "id": "init", "method": "initialize",
            "params": {"protocolVersion": "2025-11-25"}})
        version = json.loads((PLUGIN / ".codex-plugin/plugin.json").read_text())["version"]
        self.assertEqual(status, 200)
        self.assertEqual(message["result"]["serverInfo"]["version"], version)
        self.assertNotIn("Mcp-Session-Id", headers)
        self.assertEqual(self.http(payload={"jsonrpc": "2.0", "method": "notifications/initialized"})[0], 202)
        status, _, message = self.http(payload={"jsonrpc": "2.0", "id": 2, "method": "resources/read",
            "params": {"uri": "ui://task-progress/progress-inspector/v3"}})
        self.assertEqual(status, 200)
        html = message["result"]["contents"][0]["text"]
        self.assertIn(f"version: '{version}'", html)
        self.assertNotIn("__TASK_PROGRESS_VERSION__", html)
        self.assertEqual(self.http("GET", "/mcp")[0], 405)
        self.assertEqual(self.http("DELETE", "/mcp")[0], 405)

    def test_credentials_host_and_origin_are_checked_before_writes(self):
        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "init_task_network", "arguments": {"task_title": "Must not exist", "atomic_reason": "test"}}}
        for headers, expected in [({"Authorization": "wrong"}, 401), ({"Authorization": "Bearer café"}, 401),
                                  ({"Origin": "https://untrusted.example"}, 403), ({"Origin": "null"}, 403),
                                  ({"Host": "untrusted.example"}, 403)]:
            with self.subTest(headers=list(headers)):
                self.assertEqual(self.http(payload=payload, headers=headers)[0], expected)
        self.assertFalse(list((self.root / "sessions").glob("*.json")))
        self.assertEqual(self.http("OPTIONS", "/mcp")[0], 403)

    def test_bad_requests_do_not_break_next_client(self):
        self.assertEqual(self.http(raw="not-json")[0], 400)
        self.assertEqual(self.http(payload={}, headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.http(payload={}, headers={"MCP-Protocol-Version": "1900-01-01"})[0], 400)
        self.assertEqual(self.http(payload={}, headers={"Accept": "text/html"})[0], 406)
        self.assertEqual(self.http(raw="", headers={"Content-Length": str(65*1024*1024)})[0], 413)
        self.assertEqual(self.http(payload={"jsonrpc": "2.0", "id": 1, "method": "ping"})[0], 200)

    def test_duplicate_daemon_is_rejected_even_on_another_port(self):
        second = subprocess.run(self.command(), capture_output=True, text=True, timeout=3)
        self.assertEqual(second.returncode, 1)
        self.assertIn("already owns", second.stderr)
        self.assertEqual(self.http("GET", "/healthz")[2]["pid"], self.proc.pid)

    def test_normal_stop_and_crash_restart_preserve_data_and_release_lock(self):
        result = self.rpc("init_task_network", {"task_title": "Survives restart", "atomic_reason": "test"})
        sid = result["session_id"]; pid = self.proc.pid; port = self.port
        self.stop()
        self.assertFalse((self.state / "daemon.json").exists())
        self.start(port=port)
        self.assertNotEqual(self.proc.pid, pid)
        self.assertEqual(self.rpc("get_progress", {"session_id": sid, "view": "full"})["progress"]["task_title"], "Survives restart")
        self.proc.kill(); self.proc.communicate(timeout=3); self.proc = None
        self.start(port=port)
        self.assertEqual(self.rpc("get_progress", {"session_id": sid, "view": "full"})["progress"]["task_instance_id"], result["task_instance_id"])

    def test_client_disconnect_does_not_stop_global_service(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        connection.connect(); connection.close()
        self.assertEqual(self.http("GET", "/healthz")[2]["pid"], self.proc.pid)

    @unittest.skipUnless(os.name == "posix", "POSIX file permissions")
    def test_public_credentials_are_rejected(self):
        self.stop(); (self.state / "http-headers.json").chmod(0o644)
        result = subprocess.run(self.command(), capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 1)
        self.assertIn("0600", result.stderr)

    def test_plugin_configuration_starts_bridge_from_plugin_root(self):
        config = json.loads((PLUGIN / ".mcp.json").read_text())["mcpServers"]["task_progress"]
        self.assertEqual(config["command"], "python")
        self.assertEqual(config["args"], ["mcp_bridge.py"])
        self.assertEqual(config["cwd"], ".")


    def test_client_sources_isolate_catalogues_details_and_survive_restart(self):
        def create(source):
            _, _, wire = self.http(headers={'X-Task-Progress-Source':source}, payload={
                'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'init_task_network',
                'arguments':{'task_title':'Same title','atomic_reason':'Source fixture'}}})
            self.assertNotIn('error',wire)
            return wire['result']['structuredContent']['session_id']
        codex, cindy = create('codex'), create('cindy')
        old = self.rpc('init_task_network',{'task_title':'Unclassified','atomic_reason':'Legacy fixture'})['session_id']
        tokens = {}
        for source,sid in [('codex',codex),('cindy',cindy),('unknown',old)]:
            status,_,link = self.http(path='/widget/open',payload={'source':source})
            self.assertEqual(status,200)
            tokens[source] = {'Authorization':'Bearer '+link['url'].split('#')[1]}
            page = self.http('GET','/widget/catalogue?limit=1',headers=tokens[source])[2]
            self.assertEqual(page['total'],1)
            self.assertEqual(page['tasks'][0]['session_id'],sid)
        self.assertNotEqual(tokens['codex'],tokens['cindy'])
        def read(headers,sid):
            return self.http(path='/widget/rpc',headers=headers,payload={'jsonrpc':'2.0','id':2,
                'method':'tools/call','params':{'name':'get_progress_view','arguments':{'session_id':sid}}})[2]
        self.assertNotIn('error',read(tokens['cindy'],cindy))
        for headers,sid in [(tokens['cindy'],codex),(tokens['cindy'],old),(tokens['unknown'],cindy)]:
            self.assertIn('error',read(headers,sid))
        self.assertEqual(self.http(path='/widget/open',payload={'source':'codex','session_id':cindy})[0],400)
        self.assertEqual(self.http('GET','/widget/catalogue?source=codex',headers=tokens['cindy'])[0],400)
        foreign = self.http(headers={'X-Task-Progress-Source':'codex'},payload={'jsonrpc':'2.0','id':3,
            'method':'tools/call','params':{'name':'get_progress','arguments':{'session_id':cindy}}})[2]
        self.assertIn('error',foreign)
        wrong_archive = self.http(headers={'X-Task-Progress-Source':'cindy'},payload={
            'jsonrpc':'2.0','id':4,'method':'task-progress/archive-host-session',
            'params':{'session_id':codex,'host_session_id':'wrong-binding','source':'cindy'}})[2]
        self.assertIn('error',wrong_archive)
        self.assertFalse((self.root/'sessions'/(codex+'.host-archive.json')).exists())
        self.stop();self.start()
        token = self.http(path='/widget/open',payload={'source':'cindy'})[2]['url'].split('#')[1]
        page = self.http('GET','/widget/catalogue',headers={'Authorization':'Bearer '+token})[2]
        self.assertEqual([r['session_id'] for r in page['tasks']],[cindy])

    def test_legacy_source_registration_uses_explicit_ids_and_cannot_reassign(self):
        old = self.rpc('init_task_network',{'task_title':'Legacy','atomic_reason':'Old fixture'})['session_id']
        path = self.root/'sessions'/(old+'.json');before = path.read_bytes()
        def register(source,ids):
            return self.http(headers={'X-Task-Progress-Source':source},payload={'jsonrpc':'2.0','id':1,
                'method':'task-progress/register-source','params':{'source':source,'session_ids':ids}})[2]
        missing = '00000000-0000-4000-8000-000000000099'
        rows = register('cindy',[old,missing])['result']['sessions']
        self.assertEqual([r['registered'] for r in rows],[True,False])
        self.assertTrue(register('cindy',[old])['result']['sessions'][0]['registered'])
        self.assertFalse(register('codex',[old])['result']['sessions'][0]['registered'])
        self.assertEqual(before,path.read_bytes())
        self.assertFalse((self.root/'sessions'/(missing+'.source.json')).exists())

    def test_real_bridges_send_distinct_host_sources_to_http(self):
        # Importing the bridge does not run main/install. Its actual forward()
        # talks to this test's isolated HTTP daemon, not the native user job.
        from unittest.mock import patch
        import importlib.util
        for source,directory in [('codex',PLUGIN),('cindy',PLUGIN/'cindy/core')]:
            with self.subTest(source=source), patch.dict(os.environ,{'TASK_PROGRESS_SOURCE':'codex'}):
                sys.path.insert(0,str(directory))
                try:
                    spec = importlib.util.spec_from_file_location('source_bridge_'+source,directory/'mcp_bridge.py')
                    bridge = importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)
                finally:
                    sys.path.pop(0)
                with patch.object(bridge,'PORT',self.port):
                    wire = bridge.forward({'jsonrpc':'2.0','id':1,'method':'tools/call',
                        'params':{'name':'init_task_network','arguments':{'task_title':'Bridge fixture',
                        'atomic_reason':'One source fixture'}}},self.authorization)
                self.assertNotIn('error',wire)
                sid = wire['result']['structuredContent']['session_id']
                persisted = json.loads((self.root/'sessions'/(sid+'.source.json')).read_text())
                self.assertEqual(persisted['source'],source)


if __name__ == "__main__":
    unittest.main()
