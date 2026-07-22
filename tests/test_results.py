from ssh_winrm_mcp.results import RemoteResult, truncate_text


def test_truncation_keeps_head_and_tail():
    value = "a" * 100 + "z" * 100
    output, truncated = truncate_text(value, 40)
    assert truncated is True
    assert output.startswith("a" * 20)
    assert output.endswith("z" * 20)


def test_result_shape():
    result = RemoteResult(protocol="ssh", host="x", command="true", exit_code=0, ok=True)
    payload = result.to_dict(output_limit=100)
    assert payload["ok"] is True
    assert payload["output_truncated"] is False
