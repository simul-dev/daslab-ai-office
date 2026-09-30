"""Delivery orchestration uses pinned previews, never a second model rewrite."""
import unittest
from unittest.mock import Mock

from office.delivery import DeliveryError
import test_organization_development as development_tests


class OrganizationDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = development_tests.OrganizationDevelopmentTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.engine = self.fixture.engine
        self.source = self.fixture.until(self.fixture.submit(), {'review', 'failed'})['mission']
        self.attempt_id = self.source['delivery']['attempt_id']
        self.calls_before = len(self.fixture.worker.calls)
        self.engine.git_delivery = Mock()
        self.receipt = {'operation': 'push', 'applied_to_live': True, 'commit': 'a' * 40,
                        'pushed': True, 'stage': 'pushed', 'branch': 'main', 'remote': 'origin'}
        self.engine.git_delivery.deliver.return_value = self.receipt

    def submit(self, text='이 미리보기를 반영하고 커밋하고 푸시해 줘', **extra):
        payload = {'text': text, 'employee_id': 'das-pm', 'request_id': 'delivery-one',
                   'context': {'project_id': 'office-ui'}, 'source_attempt_id': self.attempt_id}
        payload.update(extra)
        return self.engine.submit(payload)['mission']['id']

    def test_explicit_followup_delivers_exact_preview_without_model_call(self):
        self.fixture.worker.available = False
        mid = self.submit()
        detail = self.fixture.until(mid, {'delivered', 'blocked', 'failed'})
        self.assertEqual(detail['mission']['status'], 'delivered', detail)
        self.assertEqual(len(self.fixture.worker.calls), self.calls_before)
        call = self.engine.git_delivery.deliver.call_args
        self.assertEqual(call.kwargs['operation'], 'push')
        self.assertIn(b'#eaf3fb', call.kwargs['files']['office.css'])
        self.assertEqual(detail['mission']['release']['commit'], 'a' * 40)
        self.assertNotEqual(detail['mission']['verification'], 'structural_only')
        self.assertEqual(self.engine.snapshot()['metrics']['verified_outcomes'], 0)
        self.assertEqual(self.engine._daily_used(), self.calls_before)

    def test_missing_preview_is_reported_before_execution(self):
        with self.assertRaises(ValueError):
            self.submit(source_attempt_id='0' * 32)
        self.engine.git_delivery.deliver.assert_not_called()

    def test_negative_or_explanatory_text_never_authorizes_delivery(self):
        for text in ('커밋이나 푸시는 하지 말고 설명해 줘', '수정된 디자인 커밋 방법을 알려줘',
                     '미리보기만 만들고 운영 화면에는 반영하지 마', 'Do not push this preview'):
            self.assertIsNone(self.engine._delivery_operation(text), text)

    def test_idempotency_rejects_changed_delivery_operation(self):
        mid = self.submit(delivery_operation='push')
        self.fixture.until(mid, {'delivered', 'blocked', 'failed'})
        from office.store import Conflict
        with self.assertRaises(Conflict):
            self.submit(delivery_operation='apply')

    def test_partial_receipt_is_kept_and_reused_on_resume(self):
        partial = dict(self.receipt, pushed=False, stage='committed')

        def fail(**kwargs):
            kwargs['on_progress'](partial)
            raise DeliveryError('Remote temporarily unavailable', partial)

        self.engine.git_delivery.deliver.side_effect = fail
        mid = self.submit()
        detail = self.fixture.until(mid, {'blocked', 'failed'})
        self.assertEqual(detail['mission']['status'], 'blocked', detail)
        self.assertEqual(detail['mission']['release']['commit'], 'a' * 40)
        self.assertIn('Remote', detail['mission']['summary'])
        self.engine.git_delivery.deliver.side_effect = None
        self.engine.action(mid, {'action': 'resume'})
        detail = self.fixture.until(mid, {'delivered', 'blocked', 'failed'})
        self.assertEqual(detail['mission']['status'], 'delivered', detail)
        previous = self.engine.git_delivery.deliver.call_args.kwargs['previous']
        self.assertEqual(previous['commit'], partial['commit'])
        self.assertEqual(previous['stage'], 'committed')
        self.assertEqual(len(self.fixture.worker.calls), self.calls_before)

    def test_source_change_after_submit_blocks_delivery(self):
        # Queue while holding the dispatcher lock, then alter the pinned source.
        with self.engine.changed:
            mid = self.submit()
            folder = self.engine.data_dir / 'organization-runs' / self.attempt_id
            css = folder / 'workspace/static/office.css'
            css.write_text('body {color: red}', encoding='utf-8')
        detail = self.fixture.until(mid, {'blocked', 'failed'})
        self.assertEqual(detail['mission']['status'], 'blocked')
        self.engine.git_delivery.deliver.assert_not_called()


if __name__ == '__main__':
    unittest.main()
