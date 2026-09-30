"""Durable PM orchestration with scripted decisions, browser receipts and Git.

All files and state live in disposable fixtures. No AI, browser or remote Git
operation is invoked by this suite.
"""
import copy
import json
import sqlite3
import unittest
from contextlib import closing
from unittest.mock import Mock

from office.organization import OrganizationEngine
from office.store import Conflict
import test_organization_development as development_tests


def pm_decision(action):
    return {'action': action, 'summary': '테스트 PM 판단: ' + action,
            'instruction': '요청한 조직 화면을 수정하고 동작 근거를 제출하세요.',
            'criteria': ['요청한 조직 화면 수정', '화면 기본 동작 검사 통과'],
            'reason': '제공된 작업본과 서버 검사 기록을 기준으로 판단했습니다.'}


class DecisionWorker(development_tests.FakeDevWorker):
    def __init__(self):
        super().__init__()
        self.decisions = ['delegate', 'accept']
        self.decision_calls = []
        self.on_decision = None
        self.decision_quota = False

    def execute_decision(self, run_dir, context, timeout_seconds, cancel_event, on_event=None):
        self.decision_calls.append(copy.deepcopy(context))
        if self.decision_quota:
            return {'completed': False, 'exit_code': 1, 'error': 'usage limit reached'}
        if not self.decisions:
            raise AssertionError('Unexpected extra PM decision')
        action = self.decisions.pop(0)
        if self.on_decision:
            self.on_decision(context)
        (run_dir / 'result.json').write_text(json.dumps(pm_decision(action)), encoding='utf-8')
        (run_dir / 'events.jsonl').write_text('{"type":"turn.completed"}\n', encoding='utf-8')
        return {'completed': True, 'exit_code': 0, 'error': None}

    def execute(self, run_dir, prompt, timeout_seconds, cancel_event, on_event=None, workspace_dir=None):
        self.entered.set()
        while not self.release.wait(.01):
            if cancel_event.is_set():
                return {'completed': False, 'exit_code': 1, 'error': 'Fixture cancelled'}
        return super().execute(run_dir, prompt, timeout_seconds, cancel_event, on_event, workspace_dir)


class ReceiptBrowser:
    def __init__(self):
        self.statuses = ['passed']
        self.calls = []
        self.on_check = None

    def verify(self, url, artifacts, folder, cancel_event):
        self.calls.append({'url': url, 'artifacts': dict(artifacts), 'folder': folder})
        if self.on_check:
            self.on_check()
        if not self.statuses:
            raise AssertionError('Unexpected extra browser check')
        status = self.statuses.pop(0)
        return {'status': status, 'summary': '테스트 화면 검사: ' + status,
                'artifacts': dict(artifacts), 'checks': ['fixture navigation'],
                'errors': [] if status == 'passed' else ['fixture failure']}


class ManagedPMTests(unittest.TestCase):
    def setUp(self):
        self.fixture = development_tests.OrganizationDevelopmentTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.engine = self.fixture.engine
        self.worker = DecisionWorker()
        self.fixture.worker = self.worker
        self.engine.worker = self.worker
        self.engine.development_policy['managed_pm'] = {'enabled': True, 'max_revisions': 2}
        self.browser = ReceiptBrowser()
        self.engine.management.browser = self.browser
        self.git = Mock()
        self.git.deliver.return_value = {'operation': 'push', 'applied_to_live': True, 'commit': 'a' * 40,
                                        'pushed': True, 'stage': 'pushed', 'branch': 'main', 'remote': 'origin'}
        self.engine.git_delivery = self.git

    def payload(self, **extra):
        payload = {'text': '조직 화면 디자인을 개선하고 실제 동작을 확인해 줘', 'employee_id': 'das-pm',
                   'request_id': 'managed-one', 'context': {'project_id': 'office-ui'}}
        payload.update(extra)
        return payload

    def submit(self, **extra):
        return self.engine.submit(self.payload(**extra))['mission']['id']

    def until(self, mid, states=('accepted', 'delivered', 'blocked', 'failed', 'deferred')):
        return self.fixture.until(mid, set(states))

    def child(self, mid):
        parent = self.engine.detail(mid)['mission']
        return self.engine.detail(parent['workflow']['current_child_id'])['mission']

    def source_css(self, mid):
        source_id = self.engine.detail(mid)['mission']['source_attempt_id']
        return self.engine.data_dir / 'organization-runs' / source_id / 'workspace/static/office.css'

    def mutate_source(self, mid):
        path = self.source_css(mid)
        path.write_text(path.read_text(encoding='utf-8') + '\nbody {color: red}', encoding='utf-8')

    def test_plan_revise_review_and_owner_authorized_delivery(self):
        self.worker.decisions = ['delegate', 'revise', 'accept']
        self.browser.statuses = ['failed', 'passed']
        mid = self.submit(text='조직 화면 디자인을 개선하고 화면과 동작을 확인하고 커밋·푸시까지 해줘')
        detail = self.until(mid)
        parent = detail['mission']
        self.assertEqual(parent['status'], 'delivered', detail)
        self.assertEqual(parent['workflow']['round'], 1)
        children = [self.engine.detail(cid)['mission'] for cid in parent['workflow']['child_ids']]
        self.assertEqual([child['execution_mode'] for child in children], ['development', 'development', 'delivery'])
        self.assertEqual([child['employee_id'] for child in children], ['das-rd', 'das-rd', 'das-pm'])
        self.assertEqual([context['stage'] for context in self.worker.decision_calls], ['plan', 'review', 'review'])
        self.assertEqual(self.worker.decision_calls[1]['browser_verification']['status'], 'failed')
        self.assertEqual(self.worker.decision_calls[2]['browser_verification']['status'], 'passed')
        self.assertEqual(len(self.worker.calls), 2)
        self.assertEqual(len(self.browser.calls), 2)
        self.assertEqual(children[1]['source_attempt_id'], children[0]['delivery']['attempt_id'])
        self.git.deliver.assert_called_once()
        self.assertEqual(self.git.deliver.call_args.kwargs['operation'], 'push')
        self.assertEqual(parent['release']['commit'], 'a' * 40)
        self.assertEqual(parent['workflow']['stage'], 'done')
        self.assertEqual(self.engine._daily_used(), 5)  # 3 PM + 2 R&D; no browser/Git charge.
        self.assertTrue(any(event['type'] == 'assistant.report' for event in detail['events']))

    def test_assistant_request_finishes_preview_without_granting_git_permission(self):
        mid = self.submit(employee_id='assistant')
        parent = self.until(mid)['mission']
        self.assertEqual(parent['status'], 'accepted', parent)
        self.assertEqual(parent['employee_id'], 'das-pm')
        self.assertEqual(parent['requested_employee_id'], 'assistant')
        self.assertNotIn('delivery_operation', parent)
        self.assertEqual(parent['verification'], 'pm_review_and_browser_smoke')
        self.assertTrue(parent['preview_available'])
        self.git.deliver.assert_not_called()

    def test_review_source_diff_preserves_unchanged_crlf_html_and_real_css_change(self):
        html_path = self.fixture.root / 'static/office.html'
        html = html_path.read_bytes().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
        self.assertIn(b'\r\n', html)
        html_path.write_bytes(html)
        (self.fixture.root / 'static/office.css').write_bytes(b'body { color: #111; }\r\n')
        mid = self.submit()
        parent = self.until(mid)['mission']
        self.assertEqual(parent['status'], 'accepted', parent)
        review = next(context for context in self.worker.decision_calls if context['stage'] == 'review')
        changes = {item['file']: item for item in review['source_changes']}
        self.assertEqual(changes['office.html']['current_vs_preview_diff'], '')
        self.assertFalse(changes['office.html']['truncated'])
        self.assertIn('+body { background: #eaf3fb; }', changes['office.css']['current_vs_preview_diff'])
        self.assertFalse(changes['office.css']['truncated'])
        source = self.engine.data_dir / 'organization-runs' / parent['source_attempt_id']
        self.assertEqual((source / 'workspace/static/office.html').read_bytes(), html)

    def test_accept_without_work_is_blocked_at_planning_stage(self):
        self.worker.decisions = ['accept']
        mid = self.submit()
        parent = self.until(mid)['mission']
        self.assertEqual(parent['status'], 'blocked', parent)
        self.assertEqual(parent['workflow']['stage'], 'planning')
        self.assertIn('실제 작업 결과 없이', parent['error'])
        self.assertFalse(self.worker.calls)
        self.assertFalse(self.browser.calls)
        self.git.deliver.assert_not_called()

    def test_pm_accept_cannot_override_failed_browser_check(self):
        self.browser.statuses = ['failed']
        mid = self.submit(text='조직 화면 디자인을 개선하고 푸시까지 해줘')
        parent = self.until(mid)['mission']
        self.assertEqual(parent['status'], 'blocked', parent)
        self.assertEqual(parent['workflow']['stage'], 'reviewing')
        self.assertIn('화면 검사를 통과하지 않아', parent['error'])
        self.git.deliver.assert_not_called()

    def test_revision_limit_stops_without_extra_child(self):
        self.engine.development_policy['managed_pm']['max_revisions'] = 1
        self.worker.decisions = ['delegate', 'revise', 'revise']
        self.browser.statuses = ['failed', 'failed']
        mid = self.submit()
        parent = self.until(mid)['mission']
        self.assertEqual(parent['status'], 'blocked', parent)
        self.assertEqual(parent['workflow']['stage'], 'reviewing')
        self.assertEqual(parent['workflow']['round'], 1)
        self.assertEqual(len(parent['workflow']['child_ids']), 2)
        self.assertIn('수정 횟수 한도', parent['error'])
        self.assertEqual(len(self.worker.calls), 2)

    def test_pause_resume_and_child_action_boundary(self):
        self.worker.release.clear()
        mid = self.submit()
        self.assertTrue(self.worker.entered.wait(3))
        child_id = self.child(mid)['id']
        for action in ('pause', 'cancel', 'resume', 'reassign', 'instruct'):
            with self.subTest(action=action), self.assertRaises(Conflict):
                self.engine.action(child_id, {'action': action, 'employee_id': 'das-pm', 'text': '수정해 줘'})
        self.engine.action(mid, {'action': 'pause'})
        parent = self.until(mid, ('paused',))['mission']
        self.assertEqual(parent['workflow']['stage'], 'working')
        self.assertEqual(self.child(mid)['status'], 'paused')
        self.worker.release.set()
        self.engine.action(mid, {'action': 'resume'})
        parent = self.until(mid)['mission']
        self.assertEqual(parent['status'], 'accepted', parent)
        self.assertEqual(parent['workflow']['child_ids'], [child_id])
        self.assertEqual(len(self.worker.decision_calls), 2)

    def test_cancel_propagates_to_running_child_and_cannot_resume(self):
        self.worker.release.clear()
        mid = self.submit()
        self.assertTrue(self.worker.entered.wait(3))
        self.engine.action(mid, {'action': 'cancel'})
        parent = self.until(mid, ('cancelled',))['mission']
        self.assertEqual(self.child(mid)['status'], 'cancelled')
        self.assertEqual(parent['workflow']['stage'], 'working')
        with self.assertRaises(Conflict):
            self.engine.action(mid, {'action': 'resume'})
        self.assertFalse(self.browser.calls)
        self.git.deliver.assert_not_called()

    def test_waiting_parent_recovers_paused_and_resumes_existing_child(self):
        self.worker.release.clear()
        mid = self.submit()
        self.assertTrue(self.worker.entered.wait(3))
        child_id = self.child(mid)['id']
        self.engine.action(mid, {'action': 'pause'})
        self.until(mid, ('paused',))
        self.engine.close()
        # Simulate persisted states from a process interruption between steps.
        with closing(sqlite3.connect(self.fixture.root / 'data/organization.sqlite3')) as db, db:
            for mission_id, status in ((mid, 'waiting'), (child_id, 'queued')):
                document = json.loads(db.execute('SELECT data FROM missions WHERE id=?', (mission_id,)).fetchone()[0])
                document['status'] = status
                db.execute('UPDATE missions SET data=? WHERE id=?', (json.dumps(document), mission_id))
        self.engine = OrganizationEngine(self.fixture.root, self.fixture.root / 'data', worker=self.worker)
        self.fixture.engine = self.engine
        self.engine.management.browser = self.browser
        self.engine.git_delivery = self.git
        parent = self.engine.detail(mid)['mission']
        self.assertEqual(parent['status'], 'paused')
        self.assertEqual(self.child(mid)['status'], 'paused')
        self.assertEqual(parent['workflow']['stage'], 'working')
        self.assertEqual(parent['workflow']['current_child_id'], child_id)
        self.worker.release.set()
        self.engine.action(mid, {'action': 'resume'})
        parent = self.until(mid)['mission']
        self.assertEqual(parent['status'], 'accepted', parent)
        self.assertEqual(parent['workflow']['child_ids'], [child_id])
        self.assertEqual(len(self.worker.decision_calls), 2)

    def test_idempotent_submission_does_not_duplicate_pm_or_rd_work(self):
        payload = self.payload()
        with self.engine.changed:
            first = self.engine.submit(payload)
            second = self.engine.submit(payload)
        self.assertEqual(first['mission']['id'], second['mission']['id'])
        self.assertTrue(second['duplicate'])
        mid = first['mission']['id']
        self.assertEqual(self.until(mid)['mission']['status'], 'accepted')
        third = self.engine.submit(payload)
        self.assertEqual(third['mission']['id'], mid)
        self.assertTrue(third['duplicate'])
        self.assertEqual(len(self.worker.calls), 1)
        self.assertEqual(len(self.worker.decision_calls), 2)
        with self.assertRaises(Conflict):
            self.engine.submit(dict(payload, text='원래 요청 대신 다른 화면을 개선해 줘'))

    def test_pinned_development_request_remains_idempotent_after_rd_replaces_source(self):
        source = self.fixture.until(self.fixture.submit('das-rd', 'source'), {'review', 'failed'})['mission']
        original_id = source['delivery']['attempt_id']
        payload = self.payload(text='이 디자인의 카드 간격을 개선하고 동작을 확인해 줘', source_attempt_id=original_id)
        mid = self.engine.submit(payload)['mission']['id']
        parent = self.until(mid)['mission']
        self.assertEqual(parent['status'], 'accepted', parent)
        self.assertNotEqual(parent['source_attempt_id'], original_id)
        replay = self.engine.submit(payload)
        self.assertTrue(replay['duplicate'])
        self.assertEqual(replay['mission']['id'], mid)
        self.assertEqual(len(self.worker.calls), 2)
        self.assertEqual(len(self.worker.decision_calls), 2)

    def test_implicit_followup_uses_latest_managed_preview_without_child_ambiguity(self):
        self.worker.decisions = ['delegate', 'revise', 'accept', 'accept']
        self.browser.statuses = ['failed', 'passed', 'passed']
        mid = self.submit()
        parent = self.until(mid)['mission']
        self.assertEqual(parent['status'], 'accepted', parent)
        latest = parent['source_attempt_id']
        self.assertEqual(len(parent['workflow']['child_ids']), 2)
        next_mid = self.submit(text='수정된 디자인을 확인하고 반영해 줘', request_id='followup-latest')
        followup = self.until(next_mid)['mission']
        self.assertEqual(followup['status'], 'delivered', followup)
        self.assertEqual(followup['source_attempt_id'], latest)
        self.assertEqual(len(self.worker.calls), 2)
        self.assertEqual(self.git.deliver.call_args.kwargs['operation'], 'apply')

    def test_parent_elapsed_includes_children_and_global_metrics_do_not_double_count(self):
        mid = self.submit()
        parent = self.until(mid)['mission']
        self.assertEqual(parent['status'], 'accepted', parent)
        ids = [mid, *parent['workflow']['child_ids']]
        with self.engine.changed, self.engine.db:
            attempts = [attempt for mission_id in ids for attempt in self.engine._attempts(mission_id)]
            expected_by_employee = {}
            for index, attempt in enumerate(attempts, 1):
                attempt['duration_seconds'] = index * 2
                expected_by_employee[attempt['employee_id']] = expected_by_employee.get(attempt['employee_id'], 0) + index * 2
                self.engine._save('attempts', attempt)
        expected = sum(expected_by_employee.values())
        detail = self.engine.detail(mid)
        snapshot = self.engine.snapshot()
        self.assertEqual(detail['mission']['elapsed_seconds'], expected)
        self.assertEqual(detail['mission']['attempts_count'], len(attempts))
        self.assertEqual(snapshot['metrics']['execution_seconds'], expected)
        self.assertGreater(detail['children'][0]['elapsed_seconds'], 0)
        for employee in snapshot['employees']:
            self.assertEqual(employee['metrics']['execution_seconds'], expected_by_employee.get(employee['id'], 0))

    def test_daily_quota_defers_child_and_resume_preserves_plan(self):
        self.engine.config['daily_runs'] = 1
        mid = self.submit()
        parent = self.until(mid)['mission']
        self.assertEqual(parent['status'], 'deferred', parent)
        self.assertEqual(parent['workflow']['stage'], 'working')
        self.assertEqual(self.child(mid)['status'], 'deferred')
        self.assertEqual(len(self.worker.decision_calls), 1)
        self.assertFalse(self.worker.calls)
        self.engine.config['daily_runs'] = 10
        self.engine.action(mid, {'action': 'resume'})
        parent = self.until(mid)['mission']
        self.assertEqual(parent['status'], 'accepted', parent)
        self.assertEqual(self.engine._daily_used(), 3)
        self.assertEqual(len(parent['workflow']['child_ids']), 1)

    def test_additional_instruction_revokes_push_and_replans_without_new_authority(self):
        self.worker.release.clear()
        mid = self.submit(text='조직 화면 디자인을 개선하고 푸시까지 해줘')
        self.assertTrue(self.worker.entered.wait(3))
        self.engine.action(mid, {'action': 'instruct', 'text': '푸시는 하지 말고 미리보기만 완성해 줘'})
        parent = self.until(mid, ('paused',))['mission']
        self.assertIsNone(parent.get('delivery_operation'))
        self.worker.decisions = ['delegate', 'accept']
        self.worker.release.set()
        self.engine.action(mid, {'action': 'resume'})
        parent = self.until(mid)['mission']
        self.assertEqual(parent['status'], 'accepted', parent)
        self.assertEqual(len(parent['workflow']['child_ids']), 2)
        self.assertEqual([context['stage'] for context in self.worker.decision_calls], ['plan', 'plan', 'review'])
        self.assertIn('푸시는 하지 말고', self.worker.decision_calls[1]['mission']['instructions'][-1]['text'])
        self.git.deliver.assert_not_called()

    def test_authority_preview_only_instruction_withdraws_prior_push(self):
        with self.engine.changed:
            mid = self.submit(text='조직 화면 디자인을 개선하고 푸시까지 해줘')
            self.engine.action(mid, {'action': 'instruct', 'text': '이제 미리보기만 완성해 줘'})
        parent = self.engine.detail(mid)['mission']
        self.assertEqual(parent['status'], 'paused')
        self.assertIsNone(parent.get('delivery_operation'))
        self.engine.action(mid, {'action': 'resume'})
        parent = self.until(mid)['mission']
        self.assertEqual(parent['status'], 'accepted', parent)
        self.assertIsNone(self.worker.decision_calls[0]['delivery_authorization'])
        self.git.deliver.assert_not_called()

    def test_authority_postponed_push_with_commit_only_never_pushes(self):
        source = self.fixture.until(self.fixture.submit('das-rd', 'source'), {'review', 'failed'})['mission']
        self.worker.decisions = ['accept']
        self.git.deliver.return_value.update(operation='commit', pushed=False, stage='committed')
        mid = self.submit(text='푸시는 나중에 하고 커밋만 해 줘', source_attempt_id=source['delivery']['attempt_id'])
        parent = self.until(mid)['mission']
        self.assertEqual(parent['status'], 'delivered', parent)
        self.assertEqual(parent['delivery_operation'], 'commit')
        self.assertEqual(self.worker.decision_calls[0]['delivery_authorization'], 'commit')
        self.assertEqual(self.git.deliver.call_args.kwargs['operation'], 'commit')

    def test_authority_preview_only_submission_overrides_legacy_push_inference(self):
        source = self.fixture.until(self.fixture.submit('das-rd', 'source'), {'review', 'failed'})['mission']
        mid = self.submit(text='이 미리보기만 확인하고 푸시는 나중에 해 줘',
                          source_attempt_id=source['delivery']['attempt_id'])
        parent = self.until(mid)['mission']
        self.assertEqual(parent['status'], 'accepted', parent)
        self.assertIsNone(parent.get('delivery_operation'))
        self.assertTrue(all(context['delivery_authorization'] is None for context in self.worker.decision_calls))
        self.git.deliver.assert_not_called()

    def test_authority_unrelated_negation_does_not_cancel_explicit_push(self):
        mid = self.submit(text='다른 것은 바꾸지 말고 디자인을 개선하고 푸시해 줘')
        parent = self.until(mid)['mission']
        self.assertEqual(parent['status'], 'delivered', parent)
        self.assertEqual(parent['delivery_operation'], 'push')
        self.assertEqual(self.worker.decision_calls[0]['delivery_authorization'], 'push')
        self.git.deliver.assert_called_once()
        self.assertEqual(self.git.deliver.call_args.kwargs['operation'], 'push')

    def test_unavailable_browser_blocks_and_resumes_check_without_new_rd_work(self):
        self.browser.statuses = ['unavailable', 'passed']
        mid = self.submit()
        parent = self.until(mid)['mission']
        self.assertEqual(parent['status'], 'blocked', parent)
        self.assertEqual(parent['workflow']['stage'], 'checking')
        self.assertEqual(len(self.worker.decision_calls), 1)
        self.engine.action(mid, {'action': 'resume'})
        parent = self.until(mid)['mission']
        self.assertEqual(parent['status'], 'accepted', parent)
        self.assertEqual(len(self.worker.calls), 1)
        self.assertEqual(len(self.browser.calls), 2)

    def test_provider_quota_blocks_decision_and_resume_retries_same_stage(self):
        self.worker.decision_quota = True
        mid = self.submit()
        parent = self.until(mid)['mission']
        self.assertEqual(parent['status'], 'deferred', parent)
        self.assertEqual(parent['workflow']['stage'], 'planning')
        self.assertTrue(self.engine._quota_blocked)
        self.worker.decision_quota = False
        self.engine.action(mid, {'action': 'resume'})
        parent = self.until(mid)['mission']
        self.assertEqual(parent['status'], 'accepted', parent)
        self.assertEqual([context['stage'] for context in self.worker.decision_calls], ['plan', 'plan', 'review'])

    def test_blocked_review_resume_refreshes_browser_evidence_for_same_work(self):
        self.worker.decisions = ['delegate', 'blocked', 'accept']
        self.browser.statuses = ['passed', 'passed']
        mid = self.submit(text='조직 화면을 개선하고 커밋·푸시까지 해줘')
        parent = self.until(mid)['mission']
        self.assertEqual(parent['status'], 'blocked', parent)
        self.assertEqual(parent['workflow']['stage'], 'reviewing')
        source = parent['source_attempt_id']
        original_child = parent['workflow']['current_child_id']
        self.git.deliver.assert_not_called()
        self.engine.action(mid, {'action': 'resume'})
        parent = self.until(mid)['mission']
        self.assertEqual(parent['status'], 'delivered', parent)
        self.assertEqual(parent['source_attempt_id'], source)
        self.assertEqual(parent['workflow']['child_ids'][0], original_child)
        self.assertEqual(len(self.worker.calls), 1)
        self.assertEqual(len(self.browser.calls), 2)
        self.assertNotEqual(self.browser.calls[0]['folder'], self.browser.calls[1]['folder'])
        self.assertEqual([call['stage'] for call in self.worker.decision_calls], ['plan', 'review', 'review'])
        browser_attempts = [attempt for attempt in self.engine.detail(mid)['attempts']
                            if attempt['execution_mode'] == 'browser_check']
        self.assertEqual(len(browser_attempts), 2)
        self.git.deliver.assert_called_once()

    def test_pinned_preview_skips_new_plan_and_is_reviewed_before_delivery(self):
        source = self.fixture.until(self.fixture.submit('das-rd', 'source'), {'review', 'failed'})['mission']
        self.worker.decisions = ['accept']
        mid = self.submit(text='이 개선본을 화면에서 확인하고 반영·커밋·푸시까지 해줘',
                          source_attempt_id=source['delivery']['attempt_id'])
        parent = self.until(mid)['mission']
        self.assertEqual(parent['status'], 'delivered', parent)
        self.assertEqual([context['stage'] for context in self.worker.decision_calls], ['review'])
        self.assertEqual(len(self.worker.calls), 1)
        self.assertEqual(len(self.browser.calls), 1)
        self.git.deliver.assert_called_once()

    def test_explicit_delivery_operation_retains_direct_delivery_path(self):
        source = self.fixture.until(self.fixture.submit('das-rd', 'source'), {'review', 'failed'})['mission']
        mid = self.submit(text='이 개선본을 반영해 줘', delivery_operation='apply',
                          source_attempt_id=source['delivery']['attempt_id'])
        parent = self.until(mid)['mission']
        self.assertEqual(parent['status'], 'delivered', parent)
        self.assertEqual(parent['execution_mode'], 'delivery')
        self.assertNotIn('workflow', parent)
        self.assertFalse(self.worker.decision_calls)
        self.assertFalse(self.browser.calls)
        self.assertEqual(self.git.deliver.call_args.kwargs['operation'], 'apply')

    def test_recovered_preview_is_reviewable_without_claiming_an_ai_execution(self):
        source = self.fixture.until(self.fixture.submit('das-rd', 'source'), {'review', 'failed'})['mission']
        aid = source['delivery']['attempt_id']
        with self.engine.changed, self.engine.db:
            mission = self.engine._get('missions', source['id'])
            attempt = self.engine._get('attempts', aid)
            mission['execution_mode'] = attempt['execution_mode'] = 'preview_import'
            mission['result']['summary'] = '보존한 개선본 연결. 새 AI 작업이나 검수 완료가 아닙니다.'
            attempt['duration_seconds'] = 0
            self.engine._save('missions', mission)
            self.engine._save('attempts', attempt)
        self.assertEqual(self.engine._daily_used(), 0)
        self.assertIn('보존한 개선본 연결', self.engine.detail(source['id'])['mission']['summary'])
        self.worker.decisions = ['accept']
        mid = self.submit(text='이 개선본을 실제 화면에 반영하고 화면과 동작을 확인하고 커밋·푸시까지 끝낸 뒤 결과만 보고해.')
        parent = self.until(mid)['mission']
        self.assertEqual(parent['status'], 'delivered', parent)
        self.assertEqual(parent['source_attempt_id'], aid)
        self.assertEqual([context['stage'] for context in self.worker.decision_calls], ['review'])
        self.assertEqual(len(self.browser.calls), 1)
        self.assertEqual(self.engine._daily_used(), 1)
        self.assertEqual(self.engine.snapshot()['metrics']['verified_outcomes'], 0)

    def test_source_mutation_after_submission_blocks_before_browser(self):
        source = self.fixture.until(self.fixture.submit('das-rd', 'source'), {'review', 'failed'})['mission']
        with self.engine.changed:
            mid = self.submit(text='이 개선본을 확인하고 반영해 줘', source_attempt_id=source['delivery']['attempt_id'])
            self.mutate_source(mid)
        parent = self.until(mid)['mission']
        self.assertEqual(parent['status'], 'blocked', parent)
        self.assertEqual(parent['workflow']['stage'], 'checking')
        self.assertFalse(self.browser.calls)
        self.assertFalse(self.worker.decision_calls)
        self.git.deliver.assert_not_called()

    def test_source_mutation_during_browser_check_invalidates_receipt(self):
        source = self.fixture.until(self.fixture.submit('das-rd', 'source'), {'review', 'failed'})['mission']
        with self.engine.changed:
            mid = self.submit(text='이 개선본을 확인하고 반영해 줘', source_attempt_id=source['delivery']['attempt_id'])
            self.browser.on_check = lambda: self.mutate_source(mid)
        parent = self.until(mid)['mission']
        self.assertEqual(parent['status'], 'blocked', parent)
        self.assertEqual(parent['workflow']['stage'], 'checking')
        self.assertFalse(self.worker.decision_calls)
        self.git.deliver.assert_not_called()

    def test_source_mutation_during_pm_accept_cannot_reuse_browser_receipt(self):
        source = self.fixture.until(self.fixture.submit('das-rd', 'source'), {'review', 'failed'})['mission']
        self.worker.decisions = ['accept']
        with self.engine.changed:
            mid = self.submit(text='이 개선본을 확인하고 푸시해 줘', source_attempt_id=source['delivery']['attempt_id'])
            self.worker.on_decision = lambda context: self.mutate_source(mid)
        parent = self.until(mid)['mission']
        self.assertEqual(parent['status'], 'blocked', parent)
        self.assertEqual(parent['workflow']['stage'], 'reviewing')
        self.git.deliver.assert_not_called()


if __name__ == '__main__':
    unittest.main()
