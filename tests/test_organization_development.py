"""Development delegation integration; isolated fixtures, no actual model calls."""
import http.client
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

from office.organization import OrganizationEngine
from office.store import Conflict
from server import handler_for
from test_organization import RecordingWorker


class FakeDevWorker(RecordingWorker):
    def execute(self, run_dir, prompt, timeout_seconds, cancel_event, on_event=None, workspace_dir=None):
        self.workspace = workspace_dir
        css = workspace_dir / 'static/office.css'
        css.write_text(css.read_text(encoding='utf-8') + '\nbody { background: #eaf3fb; }', encoding='utf-8')
        if on_event:
            on_event({'type': 'item.completed', 'item': {'type': 'file_change'}})
        return super().execute(run_dir, prompt, timeout_seconds, cancel_event, on_event)


class OrganizationDevelopmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        source = Path(__file__).resolve().parents[1]
        for folder in ('config', 'knowledge'):
            shutil.copytree(source / folder, self.root / folder)
        (self.root / 'static').mkdir()
        for name in ('office.html', 'office.css', 'office.js'):
            shutil.copy2(source / 'static' / name, self.root / 'static' / name)
        self.env = patch.dict(os.environ, {key: '' for key in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'AZURE_OPENAI_API_KEY')})
        self.env.start()
        self.worker = FakeDevWorker()
        self.engine = OrganizationEngine(self.root, self.root / 'data', worker=self.worker)
        self.server = None

    def tearDown(self):
        self.worker.release.set()
        self.stop_server()
        self.engine.close()
        self.env.stop()
        self.temp.cleanup()

    def submit(self, employee='das-pm', request='development-one', context=None):
        return self.engine.submit({'text': '조직 운영 화면 디자인을 개선해서 미리보기로 보여 줘', 'employee_id': employee, 'request_id': request, 'context': context if context is not None else {'project_id': 'office-ui'}})['mission']['id']

    def until(self, mission_id, states):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            detail = self.engine.detail(mission_id)
            if detail['mission']['status'] in states:
                return detail
            self.engine.wait_revision(detail['revision'], .03)
        self.fail(str(self.engine.detail(mission_id)))

    def start_server(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(Mock(), self.engine))
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        self.thread.start()

    def stop_server(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(2)
            self.server = None

    def get(self, path):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        connection.request('GET', path)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_pm_and_rd_have_actual_workspace_and_verified_preview(self):
        for employee in ('das-pm', 'das-rd'):
            mission_id = self.submit(employee, employee)
            detail = self.until(mission_id, {'review', 'failed', 'deferred'})
            self.assertEqual(detail['mission']['status'], 'review', detail)
            delivery = detail['mission']['delivery']
            self.assertTrue(delivery['ready'])
            self.assertEqual(delivery['changed_files'], ['office.css'])
            self.assertFalse(delivery['browser_verified'])
            self.assertFalse(delivery['applied_to_live'])
            self.assertTrue(self.worker.workspace.is_relative_to(self.root / 'data'))
            self.assertTrue(any(event['type'] == 'codex.item.completed' and '파일 수정' in event['message'] for event in detail['events']))
            self.assertEqual(self.engine.snapshot()['metrics']['verified_outcomes'], 0)

    def test_marketing_and_disabled_policy_denied(self):
        with self.assertRaises(PermissionError):
            self.submit('das-mkt')
        self.engine.development_policy['enabled'] = False
        with self.assertRaises(PermissionError):
            self.submit('das-pm')
        self.assertEqual(self.worker.calls, [])

    def test_spoofed_project_rejected_and_source_path_not_trusted(self):
        with self.assertRaises(ValueError):
            self.submit(context={'project_id': '../../private'})
        self.worker.available = False
        mission_id = self.submit(context={'project_id': 'office-ui', 'source': 'C:/private', 'workspace': '../escape', 'name': 'Public website'})
        mission = self.engine.detail(mission_id)['mission']
        context = mission['project_context']
        self.assertEqual(context['name'], 'DAS Lab 조직 운영 화면')
        self.assertNotIn('workspace', context)
        self.assertNotIn('C:/private', json.dumps(context))

    def test_same_request_id_cannot_change_context(self):
        self.worker.available = False
        self.submit()
        with self.assertRaises(Conflict):
            self.engine.submit({'text': '조직 운영 화면 디자인을 개선해서 미리보기로 보여 줘', 'employee_id': 'das-pm', 'request_id': 'development-one'})

    def test_readonly_worker_defers_before_execution(self):
        self.engine.close()
        self.worker = RecordingWorker()
        self.engine = OrganizationEngine(self.root, self.root / 'data', worker=self.worker)
        detail = self.until(self.submit(), {'deferred'})
        self.assertEqual(self.worker.calls, [])
        self.assertEqual(detail['attempts'], [])
        self.assertIn('개발 도구', detail['mission']['error'])

    def test_paused_reassignment_to_marketing_denied(self):
        self.worker.available = False
        mission_id = self.submit()
        self.until(mission_id, {'deferred'})
        self.engine.action(mission_id, {'action': 'pause'})
        with self.assertRaises(PermissionError):
            self.engine.action(mission_id, {'action': 'reassign', 'employee_id': 'das-mkt'})
        mission = self.engine.detail(mission_id)['mission']
        self.assertEqual(mission['employee_id'], 'das-pm')
        self.assertEqual(mission['status'], 'paused')

    def test_durable_redirect_after_engine_restart(self):
        detail = self.until(self.submit(), {'review', 'failed'})
        self.assertEqual(detail['mission']['status'], 'review', detail)
        path = detail['mission']['delivery']['preview_url']
        self.start_server()
        status, headers, _ = self.get(path)
        self.assertEqual(status, 302)
        self.stop_server()
        self.engine.close()
        self.engine = OrganizationEngine(self.root, self.root / 'data', worker=self.worker)
        self.start_server()
        status, headers, _ = self.get(path)
        self.assertEqual(status, 302)
        target = urlsplit(headers['Location'])
        self.assertEqual(target.hostname, '127.0.0.1')
        connection = http.client.HTTPConnection(target.hostname, target.port, timeout=3)
        connection.request('GET', '/')
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertIn(b'development-preview-banner', response.read())
        connection.close()
        self.assertEqual(len(self.worker.calls), 1)

    def test_restart_rejects_changed_preview_files(self):
        detail = self.until(self.submit(), {'review', 'failed'})
        self.assertEqual(detail['mission']['status'], 'review', detail)
        attempt_id = detail['mission']['delivery']['attempt_id']
        self.engine.close()
        css = self.root / 'data/organization-runs' / attempt_id / 'workspace/static/office.css'
        css.write_text(css.read_text(encoding='utf-8') + '\nbody { color: red; }', encoding='utf-8')
        self.engine = OrganizationEngine(self.root, self.root / 'data', worker=self.worker)
        with self.assertRaisesRegex(ValueError, 'no longer match'):
            self.engine.preview(attempt_id)

    def test_new_instruction_does_not_present_previous_delivery_as_current(self):
        detail = self.until(self.submit(), {'review', 'failed'})
        self.assertEqual(detail['mission']['status'], 'review', detail)
        mission_id = detail['mission']['id']
        self.engine.action(mission_id, {'action': 'instruct', 'text': '다음 수정은 카드 간격을 좁혀 주세요.'})
        mission = self.engine.detail(mission_id)['mission']
        self.assertEqual(mission['status'], 'paused')
        self.assertNotIn('delivery', mission)
        self.assertIsNone(mission['summary'])


if __name__ == '__main__':
    unittest.main()
