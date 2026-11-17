from codingagent.agent.parser import parse_action


def test_parse_plain_json():
    outcome = parse_action('{"action": "read_file", "args": {"path": "a.py"}, "reason": "看代码"}')
    assert outcome.error is None
    assert outcome.action.action.value == "read_file"
    assert outcome.action.args["path"] == "a.py"


def test_parse_fenced_and_prose():
    text = '先读文件：\n```json\n{"action": "grep", "args": {"pattern": "def"}}\n```\n以上。'
    outcome = parse_action(text)
    assert outcome.error is None
    assert outcome.action.action.value == "grep"


def test_parse_invalid_action():
    outcome = parse_action('{"action": "delete_file", "args": {}}')
    assert outcome.error is not None
    assert "read_file" in outcome.error


def test_parse_missing_action():
    outcome = parse_action('{"args": {"path": "a.py"}}')
    assert outcome.error is not None
    assert "action" in outcome.error


def test_parse_args_must_be_object():
    outcome = parse_action('{"action": "read_file", "args": ["a.py"]}')
    assert outcome.error is not None
    assert "对象" in outcome.error


def test_parse_empty():
    outcome = parse_action("   ")
    assert outcome.error is not None
