from coderio.tools.todo import TodoStore, TodoTool


def test_list_empty():
    store = TodoStore()
    tool = TodoTool(store)
    out = tool.run(action="list")
    assert "no" in out.lower() or "empty" in out.lower()


def test_add_and_update():
    store = TodoStore()
    tool = TodoTool(store)
    out = tool.run(action="add", content="task A", priority="high")
    assert "task A" in out
    out = tool.run(action="list")
    assert "task A" in out
    tool.run(action="update", index=0, status="completed")
    out = tool.run(action="list")
    assert "✓" in out


def test_delete():
    store = TodoStore()
    tool = TodoTool(store)
    tool.run(action="add", content="x", priority="low")
    tool.run(action="delete", index=0)
    assert len(store.todos) == 0


def test_invalid_action():
    store = TodoStore()
    tool = TodoTool(store)
    out = tool.run(action="bogus")
    assert "error" in out.lower() or "unknown" in out.lower()
