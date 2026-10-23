"""Offline tests for the physical readback; no fabricated live Qwen verdict."""
import json
import sqlite3

from evals.s25_02_step5_physical_readback import CASES, check_db


def fixture_db(path, *, corrupt_plan=False):
    with sqlite3.connect(path) as db:
        db.executescript("""
        CREATE TABLE messages(id TEXT,conversation_id TEXT,role TEXT,content TEXT,
                              status TEXT,created_at TEXT);
        CREATE TABLE turns(conversation_id TEXT,assistant_message_id TEXT,
                           state TEXT,failure_code TEXT,output_chars INTEGER);
        CREATE TABLE conversation_plans(conversation_id TEXT,snapshot TEXT);
        CREATE TABLE conversation_tasks(conversation_id TEXT,title TEXT);
        """)
        for label, prompts in CASES.items():
            for idx, prompt in enumerate(prompts):
                db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?)", (
                    label+"-user-"+str(idx),label,"user",prompt,"completed",
                    "2026-10-23T06:00:%02d" % idx))
                db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?)", (
                    label+"-assistant-"+str(idx),label,"assistant","ok",
                    "completed","2026-10-23T06:00:%02d" % idx))
                db.execute("INSERT INTO turns VALUES (?,?,?,?,?)", (
                    label,label+"-assistant-"+str(idx),"completed",None,2))
        for label in ("plan", "probe"):
            steps = [
                {"id": 1, "goal": "查询 RAG", "expected_tool":"mcp_reference_lookup",
                 "expected_arguments":{"term":"RAG"},
                 "status":"completed" if label=="plan" else "pending"},
                {"id": 2, "goal": "计算", "expected_tool":"calculate",
                 "expected_arguments":{"expression":"9*9" if label=="plan" else "8*8"},
                 "status":"completed" if label=="plan" else "pending"}]
            if label == "plan":
                for step in steps:
                    step["evidence_tool"] = step["expected_tool"]
            if corrupt_plan and label == "probe":
                steps[0]["status"] = "completed"
            db.execute("INSERT INTO conversation_plans VALUES (?,?)", (
                label, json.dumps({"revision":2,"steps":steps})))
        db.execute("INSERT INTO conversation_tasks VALUES (?,?)",
                   ("task","周五提交测试报告"))


def live_meta():
    return {"rows":[{}]*14,"model":{"provider":"ollama_proxy"},
            "summary":{"FAIL":0,"BLOCKED":0}}


def test_all_expected_physical_rows_pass(tmp_path):
    path = tmp_path / "physical.db"
    fixture_db(path)
    result = check_db(path, live_meta())
    assert result["summary"] == {"PASS":15,"FAIL":0,"BLOCKED":0}


def test_corrupt_probe_state_detected(tmp_path):
    path = tmp_path / "physical.db"
    fixture_db(path, corrupt_plan=True)
    result = check_db(path, live_meta())
    assert any(x["name"] == "probe_physical_plan_snapshot"
               and x["result"] == "FAIL" for x in result["checks"])


def test_absent_database_fails_closed(tmp_path):
    import pytest
    with pytest.raises(ValueError, match="database_file_missing"):
        check_db(tmp_path / "does-not-exist.db", live_meta())
