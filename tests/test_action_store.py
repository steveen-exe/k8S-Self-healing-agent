from k8s_agent.action_store import ActionStore


def test_action_store_persists_and_claims_atomically(tmp_path):
    store_path = tmp_path / "actions.sqlite3"
    first = ActionStore(store_path)
    payload = {"action_id": "a-1", "diagnosis": {"root_cause": "test"}}

    first.create("a-1", payload)
    assert ActionStore(store_path).list_by_status("pending") == {"a-1": payload}

    assert first.transition("a-1", "pending", "approved", actor="operator") == payload
    assert first.transition("a-1", "pending", "rejected") is None
    assert first.transition("a-1", "approved", "claimed", actor="agent") == payload
    assert first.transition("a-1", "approved", "claimed", actor="agent") is None
    assert first.status("a-1") == "claimed"


def test_action_store_rejects_duplicate_creation(tmp_path):
    store = ActionStore(tmp_path / "actions.sqlite3")
    store.create("a-1", {"action_id": "a-1"})

    try:
        store.create("a-1", {"action_id": "a-1"})
    except Exception as error:
        assert "UNIQUE" in str(error).upper()
    else:
        raise AssertionError("duplicate action IDs must be rejected")
