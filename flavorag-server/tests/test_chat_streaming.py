from app.api.chat import _agentic_replay_tokens


def test_agentic_replay_splits_buffered_answer_into_small_sse_deltas():
    assert _agentic_replay_tokens(["abcdef", "中文回答"], 2) == [
        "ab",
        "cd",
        "ef",
        "中文",
        "回答",
    ]


def test_agentic_replay_preserves_thinking_delta_marker():
    assert _agentic_replay_tokens(["__THINK__reasoning"], 3) == [
        "__THINK__rea",
        "__THINK__son",
        "__THINK__ing",
    ]
