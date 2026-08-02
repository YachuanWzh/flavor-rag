from app.api.chat import _agentic_replay_tokens, _neighbor_evidence_count


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


def test_neighbor_evidence_count_is_available_before_sse_finish():
    sources = [
        {"chunkId": "anchor", "neighborOf": []},
        {"chunkId": "neighbor-1", "neighborOf": ["anchor"]},
        {"chunkId": "neighbor-2", "neighborOf": ["anchor"]},
    ]

    assert _neighbor_evidence_count(sources) == 2
