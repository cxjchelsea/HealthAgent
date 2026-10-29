"""M4 isolated two-worker FastAPI HTTP against PostgreSQL.
No real model or user service; kill only the spawned test worker process.
"""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from services.db import Conversation, Turn, database


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"),
                    reason="requires isolated CI PostgreSQL")
def test_two_api_workers_share_active_turn_exclusion_and_restart(tmp_path):
    url = os.environ["TEST_POSTGRES_URL"]
    assert url.startswith("postgresql")
    port_a, port_b = free_port(), free_port()
    while port_b == port_a:
        port_b = free_port()
    env = dict(os.environ, DATABASE_URL=url, MODEL_PROVIDER="fake",
               ROUTE_ADMISSION_MODE="guarded", ROUTE_SHADOW_ENABLED="false",
               ALLOW_DEMO_IDENTITY="true", USER_TOKENS="")
    processes = []
    def spawn(port):
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "apps.api.main:app",
             "--host", "127.0.0.1", "--port", str(port), "--workers", "1"],
            cwd=str(Path(__file__).parents[1]), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        processes.append(proc)
        for _ in range(100):
            if proc.poll() is not None:
                raise AssertionError("worker_exited_early")
            try:
                with httpx.Client(timeout=1) as client:
                    if client.get(f"http://127.0.0.1:{port}/healthz").status_code == 200:
                        return proc
            except httpx.HTTPError:
                pass
            time.sleep(.1)
        raise AssertionError("worker_not_ready")

    engine, Session = database(url)
    cid = None
    try:
        first = spawn(port_a)
        second = spawn(port_b)
        with httpx.Client(timeout=15) as client:
            cid = client.post(f"http://127.0.0.1:{port_a}/conversations").json()["id"]
            with Session.begin() as db:
                from services.db import Message
                assistant = Message(conversation_id=cid, role="assistant",
                                    content="", status="pending")
                db.add(assistant)
                db.flush()
                db.add(Turn(conversation_id=cid,
                            request_id="m4-two-worker-crash-running-001",
                            assistant_message_id=assistant.id, state="running"))
            # A running Turn left by a dead worker remains DB authoritative
            # across a second worker and an actual restart. No tool dispatch.
            first.kill()
            first.wait(timeout=10)
            blocked = client.post(
                f"http://127.0.0.1:{port_b}/conversations/{cid}/agent/messages",
                json={"content": "继续执行", "client_request_id": "m4-worker-002"})
            assert blocked.status_code == 409
            assert blocked.json()["detail"] == "conversation_turn_in_progress"
            inspection = client.get(
                f"http://127.0.0.1:{port_b}/conversations/{cid}/plan/recovery-status")
            assert inspection.status_code == 200
            assert inspection.json()["active_turn_count"] == 1
            assert inspection.json()["automatic_replay_permitted"] is False
            restarted = spawn(port_a)
            again = client.post(
                f"http://127.0.0.1:{port_a}/conversations/{cid}/agent/messages",
                json={"content": "重试", "client_request_id": "m4-worker-003"})
            assert again.status_code == 409
            assert restarted.poll() is None
    finally:
        for proc in processes:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)
            if proc.stderr:
                proc.stderr.close()
        if cid:
            from services.db import Message
            from sqlalchemy import delete
            with Session.begin() as db:
                db.execute(delete(Turn).where(Turn.conversation_id == cid))
                db.execute(delete(Message).where(Message.conversation_id == cid))
                db.execute(delete(Conversation).where(Conversation.id == cid))
        engine.dispose()
