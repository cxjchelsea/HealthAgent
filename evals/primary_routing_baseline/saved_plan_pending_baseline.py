"""Read-only source instrumentation on frozen main; exercises isolated test DB.

Copy alongside 04-main-runtime-call-baseline.py into D:/HealthAgent.
Does not modify repository modules or production database.
"""
import collections
import functools
import json
import os
import subprocess
import time
import uuid
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import apps.api.main as api
from agent.config import Settings
from agent.understanding import UnderstandingService

HEAD = '41d91a3ae3fceeaa6f5401313f7471285b8cd86f'
AC2 = (
    'review_plan_cancellation', 'propose_first_route', 'review_plan_intent',
    'check_plan_edit_intent', 'classify_pending_reply',
    'review_pending_reference_lookup', 'review_unsupported_capability',
    'review_read_only_clarification', 'reconcile_execution_policy',
    'review_plan_creation_reference', 'review_missing_plan_reference',
    'review_plan_execution_authorization', 'review_plan_execution_scope',
)
EXTRA = ('propose_new_plan_route', 'propose_saved_plan_route',
         'review_plan_revision', 'decide_execution_policy_detail')
COUNTS = collections.Counter()
ERRORS = collections.Counter()
DURATIONS = collections.defaultdict(float)


def timed(name, fn):
    @functools.wraps(fn)
    async def wrapped(*args, **kwargs):
        COUNTS[name] += 1
        t0 = time.monotonic()
        try:
            return await fn(*args, **kwargs)
        except BaseException:
            ERRORS[name] += 1
            raise
        finally:
            DURATIONS[name] += (time.monotonic() - t0) * 1000
    return wrapped


def difference(before):
    return {k: COUNTS[k] - before.get(k, 0)
            for k in sorted(COUNTS) if COUNTS[k] != before.get(k, 0)}


def main():
    if os.getenv('RUN_S25_04_LIVE') != '1':
        raise SystemExit('Set RUN_S25_04_LIVE=1 for real provider measurements')
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    if head != HEAD:
        raise SystemExit(f'Wrong HEAD {head}; expected {HEAD}')
    status = subprocess.check_output(['git', 'status', '--porcelain'], text=True)
    base = Settings()
    if base.model_provider == 'fake':
        raise SystemExit('A real model provider is required')
    outdir = Path('artifacts')
    outdir.mkdir(exist_ok=True)
    db = (outdir / ('routing-saved-plan-' + uuid.uuid4().hex + '.sqlite')).resolve()
    cfg = Settings(**{**base.model_dump(), 'database_url': 'sqlite:///' + db.as_posix(),
        'routing_profile': 'unified', 'route_admission_mode': 'guarded',
        'understanding_strategy': 'structured', 'm5_unified_route_enabled': True,
        'm5_first_route_enabled': False, 'skill_strategy': 'semantic_topk'})
    records = []
    def new(client):
        response = client.post('/conversations')
        response.raise_for_status()
        return response.json()['id']
    def send(client, scenario, cid, content, **extra):
        before = dict(COUNTS)
        t0 = time.monotonic()
        try:
            response = client.post(f'/conversations/{cid}/agent/messages',
                json={'content': content, 'client_request_id': str(uuid.uuid4()), **extra})
            data = response.json()
            detail = data.get('detail') if isinstance(data.get('detail'), dict) else {}
            item = {'scenario': scenario, 'http_status': response.status_code,
                    'agent_status': data.get('status'),
                    'reason_code': detail.get('code') or data.get('termination_reason'),
                    'termination_reason': data.get('termination_reason'),
                    'trace_types': [t.get('type') for t in data.get('trace', [])],
                    'call_counts': difference(before),
                    'elapsed_ms': round((time.monotonic()-t0)*1000)}
        except Exception as exc:
            item = {'scenario': scenario, 'http_status': None,
                    'error_class': type(exc).__name__,
                    'call_counts': difference(before),
                    'elapsed_ms': round((time.monotonic()-t0)*1000)}
        records.append(item)
        return item

    with ExitStack() as stack:
        for name in AC2 + EXTRA:
            stack.enter_context(patch.object(api, name, timed(name, getattr(api, name))))
        stack.enter_context(patch.object(UnderstandingService, 'understand',
            timed('UnderstandingService.understand', UnderstandingService.understand)))
        with TestClient(api.create_app(cfg)) as client:
            create_text = '请创建一个两步可执行计划：第一步计算 3+4，第二步计算 8+9。只保存，不执行。'
            # Independent conversations avoid a failed creation poisoning later probes.
            for label, instruction in [
                ('first', '请只执行刚才保存计划的第一步，不执行第二步。'),
                ('resume', '请继续执行刚才保存计划的剩余步骤。'),
                ('revise', '请把已保存计划的第二步由计算 8+9 修改为计算 8+10，只修改，不执行。'),
                ('cancel', '请取消当前已保存计划，不要执行任何步骤。'),
                ('ordinary_with_plan', '请单独计算 (19+23)*4，不要执行或修改已保存的计划。'),
            ]:
                cid = new(client)
                created = send(client, label + '_setup_create', cid, create_text)
                if created['termination_reason'] != 'plan_created_pending_execution':
                    records.append({'scenario': label, 'skipped': True,
                                    'reason': 'setup_plan_creation_not_confirmed'})
                else:
                    send(client, label, cid, instruction)
            # Pending path: obtain a durable needs_input state before sending the answer.
            pending_cid = new(client)
            pending = send(client, 'pending_setup_missing_term', pending_cid,
                '请查询 MCP 参考目录里的词条，但我还没有提供词条名称。')
            if pending['reason_code'] == 'needs_input' or pending['termination_reason'] == 'needs_input':
                send(client, 'pending_answer', pending_cid, 'MCP')
            else:
                records.append({'scenario': 'pending_answer', 'skipped': True,
                                'reason': 'setup_pending_not_confirmed'})
    report = {
        'kind': 'runtime_semantic_call_baseline_saved_plan_pending',
        'source_head': head, 'working_tree_dirty': bool(status.strip()),
        'model_provider': cfg.model_provider, 'model': cfg.model_name,
        'scope': 'isolated SQLite, sequential real-provider HTTP turns on frozen main',
        'ac2_names': list(AC2), 'ac2_total': sum(COUNTS[n] for n in AC2),
        'ac2_counts': {n: COUNTS[n] for n in AC2},
        'other_counts': {n: COUNTS[n] for n in EXTRA + ('UnderstandingService.understand',)},
        'errors_by_function': dict(ERRORS),
        'elapsed_ms_by_function': {n: round(DURATIONS[n],1) for n in AC2+EXTRA+('UnderstandingService.understand',)},
        'requests': records,
        'limitations': ['No source changes or model stubs.',
            'Python function entry counts, not underlying model HTTP calls.',
            'A skipped scenario means setup failed; do not count it as zero calls.',
            'A successful setup does not guarantee requested downstream routing.',
            'TestClient isolated SQLite is not a process-crash or PostgreSQL test.'],
    }
    target = outdir / 'primary-routing-main-saved-plan-pending-baseline.json'
    target.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({'head': head, 'ac2_total':report['ac2_total'],
        'scenarios': [{k:x.get(k) for k in ('scenario','http_status','skipped','reason_code')}
                      for x in records], 'evidence':str(target)}, indent=2, ensure_ascii=False))

if __name__ == '__main__':
    main()