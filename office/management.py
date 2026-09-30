"""A durable, bounded PM -> R&D -> check -> review cycle on the existing queue.

Model decisions describe work; only this coordinator can create assignments or
request the owner's already-authorized delivery. All transitions share the
organization engine's SQLite transaction and condition lock.
"""
import hashlib
import difflib
import json
import re
import threading
from datetime import datetime, timezone
from uuid import uuid4

from .pm import validate_decision
from .browser_verification import BrowserVerifier
from .store import Conflict
from .development import _safe


def now():
    return datetime.now(timezone.utc).isoformat()


class PMWorkflow:
    def __init__(self, engine):
        self.engine = engine
        self.browser = BrowserVerifier()

    @property
    def policy(self):
        return self.engine.development_policy.get('managed_pm', {})

    def wants(self, payload, mode, requested):
        # Existing explicit delivery buttons remain direct, bounded handoffs.
        return (self.policy.get('enabled', False) and requested in ('assistant', 'das-pm')
                and mode in ('development', 'delivery') and 'delivery_operation' not in payload)

    @staticmethod
    def requested_operation(text):
        """Retain explicit delivery authority in a compound development request."""
        value = text.lower()
        if any(word in value for word in ('미리보기만', '미리보기까지만', '디자인까지만', 'preview only')):
            return None
        if any(word in value for word in ('방법', '설명', '가능', "don't", 'do not', 'without', 'how to')):
            return None
        if re.search(r'(반영|적용|커밋|푸시|푸쉬|apply|commit|push)[^.!?\n]{0,18}(하지|말고|말아|금지|제외|않|보류)', value):
            return None
        if not any(word in value for word in ('해', '줘', '하자')) and not re.match(r'\s*(please\s+)?(apply|commit|push)\b', value):
            return None
        if re.search(r'(커밋|commit)\s*(만|까지만)', value):
            return 'commit'
        if re.search(r'(반영|적용|apply)\s*(만|까지만)', value):
            return 'apply'
        if re.search(r'(반영|적용|커밋|푸시|푸쉬|apply|commit|push)[^.!?\n]{0,12}(나중|추후|다음에)', value):
            return None
        for operation, words in (('push', ('푸시', '푸쉬', 'push')), ('commit', ('커밋', 'commit')), ('apply', ('반영', '적용', 'apply'))):
            if any(word in value for word in words):
                return operation
        return None

    def initialize(self, mission):
        existing_delivery = mission['execution_mode'] == 'delivery'
        mission.update(execution_mode='managed', employee_id='das-pm',
                       routing='PM이 R&D 배정·화면 검사·결과 검수와 수정 지시를 맡습니다.')
        mission['workflow'] = {'stage': 'checking' if existing_delivery and mission.get('source_attempt_id') else 'planning',
                               'round': 0, 'max_revisions': min(2, max(0, int(self.policy.get('max_revisions', 2)))),
                               'child_ids': [], 'current_child_id': None, 'history': [],
                               'browser_verification': None, 'decision_count': 0}

    def history(self, parent, stage, summary):
        flow = parent['workflow']
        flow['stage'] = stage
        flow['history'].append({'at': now(), 'stage': stage, 'summary': summary})
        parent['updated_at'] = now()

    def block(self, parent, reason, status='blocked'):
        parent.update(status=status, error=reason, ended_at=now(),
                      result={'summary': reason, 'report': [], 'remaining': [reason],
                              'limitations': ['요청한 업무는 아직 완료되지 않았습니다.']})
        # Keep the resumable stage, rather than replacing it with a dead end.
        parent['workflow']['history'].append({'at': now(), 'stage': 'blocked', 'summary': reason})
        self.engine._save('missions', parent)
        self.engine._event('pm.blocked', reason, 'das-pm', parent['id'])

    def child(self, parent, instruction, criteria, delivery=False):
        e, stamp = self.engine, now()
        flow = parent['workflow']
        index = len(flow['child_ids']) + 1
        child = {'id': uuid4().hex, 'request_id': f"pm-{parent['id']}-{index}",
                 'text': instruction, 'title': ('검수본 반영' if delivery else 'R&D 작업') + f' · {parent["title"]}',
                 'employee_id': 'das-pm' if delivery else 'das-rd', 'requested_employee_id': 'das-pm',
                 'accountable_id': 'das-pm', 'parent_mission_id': parent['id'], 'status': 'queued',
                 'priority': parent['priority'], 'complexity': parent['complexity'],
                 'routing': 'PM이 원래 지시의 범위 안에서 배정했습니다.',
                 'created_at': stamp, 'updated_at': stamp, 'started_at': None, 'ended_at': None,
                 'acceptance_criteria': list(criteria), 'instructions': [], 'intervention_count': 0,
                 'pending_action': None, 'error': None, 'result': None, 'verification': 'not_verified',
                 'execution_mode': 'delivery' if delivery else 'development',
                 'project_context': parent['project_context']}
        if parent.get('source_attempt_id'):
            source = e._validated_development_source(parent['source_attempt_id'])
            child.update(source_attempt_id=source['attempt_id'], source_provenance=source)
        if delivery:
            child['delivery_operation'] = parent['delivery_operation']
            child['required_browser_artifacts'] = dict(flow['browser_verification']['artifacts'])
        e._require_developer(child['employee_id'])
        flow['child_ids'].append(child['id'])
        flow['current_child_id'] = child['id']
        parent.update(status='waiting', ended_at=None, error=None)
        self.history(parent, 'delivering' if delivery else 'working',
                     '검수한 작업본의 반영을 맡겼습니다.' if delivery else 'R&D에 작업을 맡겼습니다: ' + instruction[:250])
        e._save('missions', child)
        e._save('missions', parent)
        e._event('pm.delegated', flow['history'][-1]['summary'], 'das-pm', parent['id'])

    def finish(self, parent, summary, delivered=False):
        parent.update(status='delivered' if delivered else 'accepted', error=None, ended_at=now(),
                      verification='pm_review_and_browser_smoke',
                      result={'summary': summary, 'report': [{'title': 'PM 검수 결과', 'content': summary}],
                              'accomplishments': [summary], 'remaining': [],
                              'limitations': ['브라우저 기본 동작 검사와 PM 검수를 완료했습니다. 디자인 품질·사업 성과의 독립 검증은 아닙니다.']})
        self.history(parent, 'done', summary)
        self.engine._save('missions', parent)
        self.engine._save('memories', {'id': uuid4().hex, 'employee_id': 'das-pm', 'text': summary,
                                     'source': 'mission:' + parent['id'], 'created_at': now(),
                                     'verification': 'pm_review_and_browser_smoke'})
        self.engine._event('assistant.report', summary, 'assistant', parent['id'])

    def child_finished(self, child_id):
        e = self.engine
        child = e._get('missions', child_id)
        if not child.get('parent_mission_id'):
            return
        parent = e._get('missions', child['parent_mission_id'])
        flow = parent['workflow']
        if parent['status'] == 'pausing':
            parent.update(status=parent.get('pending_action') or 'paused', pending_action=None)
            e._save('missions', parent)
            e._event('pm.paused', 'PM과 담당 직원의 실행이 멈췄습니다.', 'das-pm', parent['id'])
            return
        if parent['status'] != 'waiting' or flow['current_child_id'] != child_id:
            return
        if child['status'] in ('queued', 'running', 'pausing'):
            return
        if child['execution_mode'] == 'delivery':
            parent['release'] = child.get('release') or {}
            if child['status'] == 'delivered':
                self.finish(parent, 'PM이 화면 기본 동작과 작업 결과를 검수했습니다. ' + child['result']['summary'], True)
            else:
                self.block(parent, '검수는 마쳤지만 반영을 완료하지 못했습니다. ' + (child.get('error') or child['status']))
            return
        if child['status'] in ('paused', 'cancelled', 'deferred'):
            self.block(parent, child.get('error') or '담당 직원의 실행이 멈췄습니다.', 'deferred' if child['status'] == 'deferred' else 'paused')
            return
        delivery = child.get('delivery') or {}
        if delivery.get('ready'):
            source = e._validated_development_source(delivery['attempt_id'])
            parent.update(source_attempt_id=source['attempt_id'], source_provenance=source, delivery=delivery)
            flow['browser_verification'] = None
            self.history(parent, 'checking', 'R&D 작업이 도착했습니다. 화면을 열어 확인합니다.')
        else:
            flow['browser_verification'] = {'status': 'failed', 'summary': child.get('error') or '검사 가능한 작업본이 없습니다.',
                                            'artifacts': {}, 'errors': [child.get('error') or '작업본 검사 실패'], 'checks': []}
            self.history(parent, 'reviewing', '작업이 검사를 통과하지 못했습니다. PM이 수정할 내용을 확인합니다.')
        parent.update(status='queued', error=None, ended_at=None)
        e._save('missions', parent)
        e._event('pm.result_received', flow['history'][-1]['summary'], 'das-pm', parent['id'])

    def action(self, parent, action, payload):
        e, flow = self.engine, parent['workflow']
        if action == 'reassign':
            raise Conflict('이 업무의 PM은 DAS Lab PM입니다. 추가 지시로 작업 방향을 바꿀 수 있습니다.')
        if parent['status'] in ('cancelled', 'accepted', 'delivered'):
            raise Conflict('끝난 업무는 새 지시로 이어 주세요.')
        children = [e._get('missions', cid) for cid in flow['child_ids']]
        active = e._active if e._active and e._active['mission_id'] in [parent['id'], *flow['child_ids']] else None
        if active and e._get('missions', active['mission_id'])['execution_mode'] == 'delivery':
            raise Conflict('파일 반영 중입니다. 현재 처리가 끝난 뒤 지시해 주세요.')
        if action == 'resume':
            if active or parent['status'] not in ('paused', 'blocked', 'deferred', 'failed'):
                raise Conflict('멈춘 업무만 재개할 수 있습니다.')
            e._set_quota_blocked(False)
            current = next((c for c in children if c['id'] == flow['current_child_id']), None)
            parent.update(status='queued', error=None, pending_action=None, ended_at=None, result=None)
            if flow.pop('restart_plan', False):
                flow.update(stage='planning', current_child_id=None, browser_verification=None)
            elif flow['stage'] in ('working', 'delivering') and current:
                parent['status'] = 'waiting'
                if current['status'] in ('paused', 'deferred', 'failed', 'blocked'):
                    current.update(status='queued', error=None, pending_action=None, ended_at=None)
                    e._save('missions', current)
                e._save('missions', parent)
                self.child_finished(current['id'])
                parent = e._get('missions', parent['id'])
            message = '저장된 단계부터 PM 업무를 이어갑니다.'
        else:
            if action == 'instruct':
                text = payload.get('text')
                if not isinstance(text, str) or not text.strip() or len(text) > 6000 or len(parent['instructions']) >= 30:
                    raise ValueError('추가 지시는 1~6000자로 입력하세요.')
                parent['instructions'].append({'text': text.strip(), 'created_at': now(), 'source': 'owner'})
                if any(word in text.lower() for word in ('반영', '적용', '커밋', '푸시', '푸쉬', 'apply', 'commit', 'push',
                                                        '미리보기만', '미리보기까지만', '디자인까지만', 'preview only')):
                    operation = self.requested_operation(text)
                    if operation:
                        policy = e.development_policy.get('delivery', {})
                        if not policy.get('enabled') or operation not in policy.get('allowed_operations', []):
                            raise PermissionError('요청한 반영 권한이 설정되지 않았습니다.')
                        parent['delivery_operation'] = operation
                    else:
                        parent.pop('delivery_operation', None)
                flow['restart_plan'] = True
                flow.pop('accepted_source', None)
            target = 'cancelled' if action == 'cancel' else 'paused'
            for child in children:
                if child['status'] in ('queued', 'running', 'pausing', 'deferred'):
                    running = active and active['mission_id'] == child['id']
                    child.update(status='pausing' if running else target, pending_action=target if running else None)
                    e._save('missions', child)
            parent.update(status='pausing' if active else target, pending_action=target if active else None)
            if active:
                active['cancel'].set()
            message = 'PM과 담당 직원의 업무를 함께 멈춥니다.'
        parent.update(intervention_count=parent['intervention_count'] + 1, updated_at=now())
        e._save('missions', parent)
        e._event('owner.' + action, message, 'das-pm', parent['id'])
        return e.detail(parent['id'])

    def run(self, mission_id):
        e = self.engine
        with e.changed, e.db:
            parent = e._get('missions', mission_id)
            if e.closed or parent['status'] != 'queued':
                return
            flow = parent['workflow']
            stage = flow['stage']
            if not self.policy.get('enabled'):
                self.block(parent, 'PM 업무 연결이 꺼져 있습니다.')
                return
            e._require_developer('das-pm')
            if stage not in ('planning', 'checking', 'reviewing'):
                self.block(parent, '저장된 다음 작업을 확인해야 합니다.')
                return
            decision_step = stage != 'checking'
            if decision_step:
                if e._quota_blocked or e._daily_used() >= e.config['daily_runs']:
                    self.block(parent, '구독 사용량 또는 내부 일일 실행 한도로 기다리고 있습니다.', 'deferred')
                    return
                if flow['decision_count'] >= 2 * flow['max_revisions'] + 4:
                    self.block(parent, 'PM 판단 횟수 한도에 도달했습니다. 남은 문제를 확인해 주세요.')
                    return
                if not callable(getattr(e.worker, 'execute_decision', None)):
                    self.block(parent, 'PM 판단을 지원하는 실행기가 연결되지 않았습니다.')
                    return
            stamp, aid = now(), uuid4().hex
            attempt = {'id': aid, 'mission_id': mission_id, 'employee_id': 'das-pm',
                       'number': len(e._attempts(mission_id)) + 1, 'status': 'running', 'started_at': stamp,
                       'ended_at': None, 'duration_seconds': 0, 'summary': None, 'error': None,
                       'execution_mode': 'pm_decision' if decision_step else 'browser_check', 'stage': stage}
            folder = e.data_dir / 'organization-runs' / aid
            folder.mkdir(parents=True, exist_ok=False)
            cancel = threading.Event()
            e._active = {'mission_id': mission_id, 'attempt_id': aid, 'employee_id': 'das-pm', 'cancel': cancel}
            parent.update(status='running', started_at=parent.get('started_at') or stamp, error=None)
            if decision_step:
                flow['decision_count'] += 1
            e._save('attempts', attempt)
            e._save('missions', parent)
            e._event('pm.started', {'planning': 'PM이 작업을 나눕니다.', 'checking': '화면을 열어 기본 동작을 검사합니다.',
                                    'reviewing': 'PM이 검사 결과와 작업 내용을 검수합니다.'}[stage], 'das-pm', mission_id)
            child = e._get('missions', flow['current_child_id']) if flow['current_child_id'] else None
            context = {'stage': 'plan' if stage == 'planning' else 'review', 'mission': parent,
                       'employee': e._employee('das-pm'), 'memories': e._memories('das-pm', 20),
                       'project_knowledge': e._knowledge, 'child': child,
                       'browser_verification': flow['browser_verification'],
                       'allowed_assignee': 'das-rd', 'max_revisions': flow['max_revisions'],
                       'delivery_authorization': parent.get('delivery_operation'),
                       'scope': '조직 UI 파일만 수정. 반영·Git은 서버가 대표의 원래 지시 범위에서 처리. 브라우저 검사는 기본 동작만 확인.'}
        decision, receipt, error = None, None, None
        try:
            if decision_step and stage == 'reviewing' and parent.get('source_attempt_id'):
                with e.lock:
                    source = e._validated_development_source(parent['source_attempt_id'])
                files, _ = e.development.delivery_files(e.data_dir / 'organization-runs' / source['attempt_id'], source['artifacts'])
                context['source_changes'] = []
                for name in ('office.html', 'office.css', 'office.js'):
                    live = (e.root / 'static' / name).read_bytes().decode('utf-8')
                    preview = files[name].decode('utf-8')
                    diff = ''.join(difflib.unified_diff(live.splitlines(True), preview.splitlines(True),
                                                     fromfile='current/' + name, tofile='preview/' + name))
                    context['source_changes'].append({'file': name, 'current_vs_preview_diff': diff[:18000],
                                                      'truncated': len(diff) > 18000})
                captures = (flow['browser_verification'] or {}).get('screenshots', [])
                for capture_name in ('desktop.png', 'mobile.png'):
                    capture = next((p for p in captures if str(p).replace('\\', '/').endswith('/' + capture_name)), None)
                    if capture:
                        path = _safe(capture, e.data_dir / 'organization-runs')
                        if path.stat().st_size <= 10_000_000:
                            (folder / ('review-' + capture_name)).write_bytes(path.read_bytes())
                context['review_images'] = '첨부된 이미지는 서버가 방금 검사한 미리보기입니다. 자동 기본 동작 검사와 시각적 판단을 구별하세요.'
            (folder / 'input.json').write_text(json.dumps(context, ensure_ascii=False), encoding='utf-8')
            if decision_step:
                if e._api_environment_present():
                    raise ValueError('API 키 환경변수가 있어 구독 전용 실행을 중지했습니다.')
                execution = e.worker.execute_decision(folder, context, e.config['timeout_seconds'], cancel,
                                                      on_event=lambda event: e._worker_event(attempt, event))
                (folder / 'execution.json').write_text(json.dumps(execution, ensure_ascii=False), encoding='utf-8')
                if not execution.get('completed') or execution.get('exit_code') != 0:
                    raise ValueError(execution.get('error') or 'PM 판단 실행을 완료하지 못했습니다.')
                path = folder / 'result.json'
                if path.is_symlink() or not path.is_file() or path.stat().st_size > 100_000:
                    raise ValueError('PM 판단 결과 파일이 올바르지 않습니다.')
                raw = path.read_bytes()
                decision = validate_decision(json.loads(raw), context['stage'])
                attempt['result_sha256'] = hashlib.sha256(raw).hexdigest()
                attempt['decision'] = decision
            else:
                with e.lock:
                    source = e._validated_development_source(parent['source_attempt_id'])
                if source != parent['source_provenance']:
                    raise ValueError('검사할 작업본이 접수한 원본과 다릅니다.')
                source_folder = e.data_dir / 'organization-runs' / source['attempt_id']
                url = e.development.open_preview(source_folder, e.snapshot, expected_artifacts=source['artifacts'])
                receipt = self.browser.verify(url, source['artifacts'], folder / 'browser', cancel)
                with e.lock:
                    if e._validated_development_source(source['attempt_id']) != source:
                        raise ValueError('화면 검사 도중 작업본이 변경되었습니다.')
                if receipt.get('artifacts') != source['artifacts']:
                    raise ValueError('화면 검사 기록의 작업본이 다릅니다.')
                attempt['browser_verification'] = receipt
        except Exception as exc:
            error = str(exc)[:2000]
        with e.changed, e.db:
            current = e._get('missions', mission_id)
            flow = current['workflow']
            attempt.update(ended_at=now(), duration_seconds=round((datetime.now(timezone.utc) - datetime.fromisoformat(stamp)).total_seconds(), 2),
                           status='failed' if error else 'completed', error=error,
                           summary=error or (decision or receipt or {}).get('summary'))
            if cancel.is_set() or current['status'] == 'pausing':
                current.update(status=current.get('pending_action') or 'paused', pending_action=None)
                attempt['status'] = 'cancelled'
                e._save('missions', current)
                e._event('pm.paused', 'PM 실행이 멈췄습니다.', 'das-pm', mission_id)
            elif error:
                quota = e._quota_failure(folder) or any(word in error.lower() for word in ('quota', 'rate limit', 'usage limit', '사용량 한도'))
                if quota:
                    e._set_quota_blocked(True)
                self.block(current, error, 'deferred' if quota else 'blocked')
            elif not decision_step:
                flow['browser_verification'] = receipt
                if receipt['status'] == 'unavailable':
                    self.block(current, receipt['summary'])
                else:
                    self.history(current, 'reviewing', receipt['summary'])
                    current['status'] = 'queued'
                    e._save('missions', current)
                    e._event('pm.browser_checked', receipt['summary'], 'das-pm', mission_id)
            else:
                try:
                    self.decide(current, decision, stage)
                except Exception as exc:
                    attempt.update(status='failed', error=str(exc)[:2000])
                    self.block(current, 'PM 후속 작업을 연결하지 못했습니다: ' + str(exc)[:1000])
            e._save('attempts', attempt)
            e._active = None

    def decide(self, parent, decision, stage):
        flow, e = parent['workflow'], self.engine
        action = decision['action']
        flow['history'].append({'at': now(), 'stage': stage, 'summary': decision['summary']})
        if action == 'blocked':
            self.block(parent, decision['reason'])
        elif action == 'delegate':
            flow['work_criteria'] = decision['criteria']
            self.child(parent, decision['instruction'], decision['criteria'])
        elif action == 'revise':
            if flow['round'] >= flow['max_revisions']:
                self.block(parent, '수정 횟수 한도에 도달했습니다. ' + decision['reason'])
                return
            flow['round'] += 1
            flow['browser_verification'] = None
            self.child(parent, decision['instruction'], flow.get('work_criteria') or decision['criteria'])
        elif stage == 'planning':
            # A plan is not evidence of completed work.
            self.block(parent, 'PM이 실제 작업 결과 없이 완료를 판단했습니다. 완료로 처리하지 않았습니다.')
        else:
            receipt = flow['browser_verification'] or {}
            if receipt.get('status') != 'passed' or not parent.get('source_attempt_id'):
                self.block(parent, '화면 검사를 통과하지 않아 완료나 반영으로 진행하지 않았습니다. ' + receipt.get('summary', ''))
                return
            source = e._validated_development_source(parent['source_attempt_id'])
            if receipt.get('artifacts') != source['artifacts'] or source != parent.get('source_provenance'):
                self.block(parent, 'PM 검수 뒤 작업본이 바뀌었습니다. 다시 확인해야 합니다.')
                return
            flow['accepted_source'] = source
            flow['review_summary'] = decision['summary']
            if parent.get('delivery_operation'):
                self.child(parent, '검수한 미리보기를 대표가 요청한 범위까지 반영합니다.', parent['acceptance_criteria'], delivery=True)
            else:
                self.finish(parent, 'PM 검수를 마쳤습니다. ' + decision['summary'] + ' 미리보기를 확인할 수 있습니다.')
