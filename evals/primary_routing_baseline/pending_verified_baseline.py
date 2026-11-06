"""Frozen-main real-provider Pending intake/resume call-count baseline.

Copy to D:/HealthAgent and run without modifying repository modules.
Only TestClient HTTP requests create conversation/turn state; isolated SQLite.
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
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import apps.api.main as api
from agent.config import Settings
from agent.understanding import UnderstandingService
from services.db import Turn, Message

HEAD = '41d91a3ae3fceeaa6f5401313f7471285b8cd86f'
AC2 = (
    'review_plan_cancellation', 'propose_first_route', 'review_plan_intent',
    'check_plan_edit_intent', 'classify_pending_reply',
    'review_pending_reference_lookup', 'review_unsupported_capability',
    'review_read_only_clarification', 'reconcile_execution_policy',
    'review_plan_creation_reference', 'review_missing_plan_reference',
    'review_plan_execution_authorization', 'review_plan_execution_scope',
)
OTHER = ('propose_new_plan_route', 'propose_saved_plan_route',
         'review_plan_revision', 'decide_execution_policy_detail')
COUNTS = collections.Counter()
ERRORS = collections.Counter()


def counted(name, fn):
    @functools.wraps(fn)
    async def wrapped(*args, **kwargs):
        COUNTS[name] += 1
        try:
            return await fn(*args, **kwargs)
        except BaseException:
            ERRORS[name] += 1
            raise
    return wrapped


def diff(old):
    return {name: COUNTS[name] - old.get(name, 0)
            for name in AC2 + OTHER + ('UnderstandingService.understand',)
            if COUNTS[name] != old.get(name, 0)}


def main():
    if os.environ.get('RUN_S25_04_LIVE') != '1':
        raise SystemExit('RUN_S25_04_LIVE=1 required')
    sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    if sha != HEAD:
        raise SystemExit(f'Wrong source HEAD: {sha}; expected {HEAD}')
    dirty = bool(subprocess.check_output(['git', 'status', '--porcelain'], text=True).strip())
    base = Settings()
    if base.model_provider == 'fake':
        raise SystemExit('Real model provider required')
    folder = Path('artifacts')
    folder.mkdir(parents=True, exist_ok=True)
    dbpath = (folder / ('pending-baseline-' + uuid.uuid4().hex + '.sqlite')).resolve()
    dburl = 'sqlite:///' + dbpath.as_posix()
    cfg = Settings(**{**base.model_dump(), 'database_url': dburl,
        'routing_profile': 'unified', 'route_admission_mode': 'guarded',
        'understanding_strategy': 'structured', 'm5_unified_route_enabled': True,
        'm5_first_route_enabled': False, 'skill_strategy': 'semantic_topk',
        'route_shadow_enabled': False, 's25_04_admission_trace_enabled': False})
    engine = create_engine(dburl)
    records = []

    def send(client, cid, label, prompt):
        before = dict(COUNTS)
        t0 = time.monotonic()
        request_id = str(uuid.uuid4())
        try:
            response = client.post(f'/conversations/{cid}/agent/messages', json={
                'content': prompt, 'client_request_id': request_id})
            payload = response.json()
            detail = payload.get('detail') if isinstance(payload.get('detail'), dict) else {}
            row = {'scenario': label, 'http_status': response.status_code,
                   'agent_status': payload.get('status'),
                   'reason_code': detail.get('code') or payload.get('termination_reason'),
                   'termination_reason': payload.get('termination_reason'),
                   'trace_types': [x.get('type') for x in (payload.get('trace') or [])],
                   'call_counts': diff(before), 'elapsed_ms': round((time.monotonic()-t0)*1000)}
        except Exception as exc:
            row = {'scenario': label, 'exception_type': type(exc).__name__,
                   'call_counts': diff(before), 'elapsed_ms': round((time.monotonic()-t0)*1000)}
        # Verify actual persisted Turn state, not just model reply or HTTP status.
        with Session(engine) as session:
            turn = session.scalar(select(Turn).where(
                Turn.conversation_id == cid, Turn.request_id == request_id))
            row['durable'] = ({'state': turn.state, 'failure_code': turn.failure_code,
                               'assistant_status': session.get(Message, turn.assistant_message_id).status}
                              if turn else None)
        records.append(row)
        return row

    # Each setup is a genuine HTTP request. One bounded group of three natural
    # variants, not prompt-searching until a lucky result. No DB state fabrication.
    setup_prompts = [
        '请使用 mcp_reference_lookup 查询一个参考词条，但我还没给出必填的 term 参数。请先询问我要查哪个词条，不要猜测。',
        '帮我查一下 MCP 参考目录里的一个概念。目前我没有告诉你词条名，请先问我词条名称，再查询。',
        '我要创建一个待办任务，但还没有告诉你任务标题。请先问我标题，不要创建空任务。',
    ]
    reached = None
    with ExitStack() as stack:
        for name in AC2 + OTHER:
            stack.enter_context(patch.object(api, name, counted(name, getattr(api, name))))
        stack.enter_context(patch.object(UnderstandingService, 'understand',
            counted('UnderstandingService.understand', UnderstandingService.understand)))
        with TestClient(api.create_app(cfg)) as client:
            for idx, prompt in enumerate(setup_prompts, 1):
                cid = client.post('/conversations').json()['id']
                setup = send(client, cid, f'pending_setup_{idx}', prompt)
                failure = ((setup.get('durable') or {}).get('failure_code') or '')
                if setup.get('http_status') == 200 and failure.startswith('needs_input:'):
                    action = failure.split(':', 1)[1]
                    if action not in {'mcp_reference_lookup', 'create_task'}:
                        continue
                    reached = {'conversation_id': cid, 'pending_action': action, 'setup_index': idx}
                    followup = 'MCP' if action == 'mcp_reference_lookup' else '提交测试报告'
                    send(client, cid, 'pending_answer', followup)
                    break
    report = {
        'kind': 'runtime_semantic_call_baseline_pending_verified',
        'source_head': sha, 'working_tree_dirty': dirty,
        'provider': cfg.model_provider, 'model': cfg.model_name,
        'scope': 'real-provider HTTP with fresh conversation per setup, isolated SQLite',
        'setup_attempt_limit': len(setup_prompts),
        'pending_reached': reached is not None,
        'pending_action': reached['pending_action'] if reached else None,
        'ac2_total': sum(COUNTS[name] for name in AC2),
        'ac2_counts': {name: COUNTS[name] for name in AC2},
        'other_counts': {name: COUNTS[name] for name in OTHER + ('UnderstandingService.understand',)},
        'errors_by_function': dict(ERRORS),
        'requests': records,
        'limitations': [
            'No repo module edits, model monkeypatch or synthetic database Pending state.',
            'Only Python function entries counted, not nested provider HTTP attempts.',
            'Bounded three setup variants. If no needs_input:<tool> is persisted, report uncovered.',
            'No PostgreSQL, process crash, or concurrent replay tested.',
            'No success claim based solely on HTTP 200 or a natural-language clarification.',
        ],
    }
    target = folder / 'primary-routing-main-pending-verified-baseline.json'
    target.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({'head': sha, 'pending_reached': report['pending_reached'],
        'pending_action': report['pending_action'], 'ac2_total': report['ac2_total'],
        'requests': [{'scenario': x['scenario'], 'http_status': x.get('http_status'),
                      'durable': x.get('durable'), 'call_counts': x['call_counts']}
                     for x in records], 'file': str(target)}, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()