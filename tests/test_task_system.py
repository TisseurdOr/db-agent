"""Task System 冒烟：create / claim / blockedBy / complete 解锁。"""

from multi_agent.task_system import TaskManager


def test_create_get_list(tmp_path):
    tm = TaskManager(tasks_dir=tmp_path)
    t = tm.create("setup schema", description="ddl")
    assert tm.get(t.id).subject == "setup schema"
    assert len(tm.list_all()) == 1


def test_claim_blocked_until_dep_done(tmp_path):
    tm = TaskManager(tasks_dir=tmp_path)
    a = tm.create("A")
    b = tm.create("B", blockedBy=[a.id])
    assert tm.can_start(a.id)
    assert not tm.can_start(b.id)
    assert "Blocked" in tm.claim(b.id)
    tm.claim(a.id)
    tm.complete(a.id)
    assert tm.can_start(b.id)
    assert "Claimed" in tm.claim(b.id, owner="sql")


def test_complete_unblocks_downstream(tmp_path):
    tm = TaskManager(tasks_dir=tmp_path)
    a = tm.create("A")
    b = tm.create("B", blockedBy=[a.id])
    tm.claim(a.id)
    msg = tm.complete(a.id)
    assert "Unblocked" in msg
    assert "B" in msg
    assert tm.get(b.id).status == "pending"


def test_materialize_from_plan_serial_deps(tmp_path):
    tm = TaskManager(tasks_dir=tmp_path)
    plan = [
        {"agent": "sql", "task": "SQL 侧列表"},
        {"agent": "hive", "task": "Hive 侧列表"},
    ]
    created = tm.materialize_from_plan(plan, query="SQL和hive有什么表")
    assert len(created) == 2
    assert created[0].status == "in_progress"
    assert created[0].agent == "sql"
    assert created[1].blockedBy == [created[0].id]
    assert created[1].status == "pending"

    tm.on_agent_finished("sql")
    assert tm.get(created[0].id).status == "completed"
    assert tm.get(created[1].id).status == "in_progress"

    tm.on_agent_finished("hive")
    assert tm.get(created[1].id).status == "completed"


def test_update_fields(tmp_path):
    tm = TaskManager(tasks_dir=tmp_path)
    t = tm.create("x")
    tm.update(t.id, subject="y", status="completed")
    assert tm.get(t.id).subject == "y"
    assert tm.get(t.id).status == "completed"
