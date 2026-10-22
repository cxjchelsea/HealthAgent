"""S25-03 Step 4 versioned task state and evidence validity contracts."""
import pytest
from services.db import database, Conversation, AgentTaskState
from services.migrate import migrate
from services.task_state import AgentTaskStateStore, project_task
from services.evidence_store import AttemptEvidenceStore, make_attempt
from services.db import Message, Turn

def test_task_state_revision_compare_and_swap(tmp_path):
    url = f"sqlite:///{tmp_path/'task_state.db'}"
    migrate(url)
    engine, Session = database(url)
    with Session.begin() as db:
        db.add(Conversation(id="c1", owner_id="u"))
    store=AgentTaskStateStore("c1")
    checkpoint={"revision":1,"steps":[{"id":1,"status":"pending"}]}
    with Session.begin() as db:
        task_id, rev=store.save_in_session(db,checkpoint,"partial",None)
    assert rev==1
    first=store.load(Session)
    assert first["task_id"]==task_id and first["steps"][0]["status"]=="pending"
    with Session.begin() as db:
        task_id2, rev2=store.save_in_session(db,checkpoint,"partial",1)
    assert task_id2==task_id and rev2==2
    with pytest.raises(RuntimeError, match="stale_task_revision"):
        with Session.begin() as db:
            store.save_in_session(db,checkpoint,"completed",1)
    assert store.load(Session)["revision"]==2
    engine.dispose()

def test_task_projection_never_marks_pending_plan_completed():
    assert project_task({"revision":2,"steps":[{"id":1,"status":"pending"}]}, "completed")[0]=="partial"

def test_old_step_evidence_requires_same_binding_and_active_completed(tmp_path):
    url=f"sqlite:///{tmp_path/'step_evidence.db'}"
    migrate(url)
    engine, Session=database(url)
    with Session.begin() as db:
        db.add(Conversation(id="c1",owner_id="u"))
        db.add(Message(id="m1",conversation_id="c1",role="assistant",content="",status="completed"))
        db.add(Turn(id="t1",conversation_id="c1",request_id="request-1",
                    assistant_message_id="m1",state="completed"))
    store=AttemptEvidenceStore("c1","t1")
    with Session.begin() as db:
        store.save_in_session(db,[make_attempt("run","calculate",{"expression":"8*8"},
                                             {"ok":True,"value":64},1)])
    good={"revision":1,"steps":[{"id":1,"status":"completed",
                                "expected_tool":"calculate","expected_arguments":{"expression":"8*8"}}]}
    bad={"revision":2,"steps":[{"id":1,"status":"completed",
                               "expected_tool":"calculate","expected_arguments":{"expression":"9*9"}}]}
    pending={"revision":2,"steps":[{"id":1,"status":"pending",
                                   "expected_tool":"calculate","expected_arguments":{"expression":"8*8"}}]}
    assert store.completed_step_projections(Session,good)[1]["result"]["value"]==64
    assert store.completed_step_projections(Session,bad)=={}
    assert store.completed_step_projections(Session,pending)=={}
    engine.dispose()
