"""测试 JSON 解析器。"""

import pytest
from app.utils.json_parser import parse_json


class TestParseJson:
    def test_plain_json_object(self):
        result = parse_json('{"key": "value", "num": 42}')
        assert result == {"key": "value", "num": 42}

    def test_plain_json_array(self):
        result = parse_json('[{"a": 1}, {"b": 2}]')
        assert result == [{"a": 1}, {"b": 2}]

    def test_markdown_code_block(self):
        result = parse_json('```json\n{"key": "value"}\n```')
        assert result == {"key": "value"}

    def test_markdown_no_lang(self):
        result = parse_json('```\n{"key": "value"}\n```')
        assert result == {"key": "value"}

    def test_with_leading_text(self):
        """有时 LLM 会在 JSON 前加说明文字。"""
        result = parse_json('这是分析结果：\n{"key": "value"}')
        assert result == {"key": "value"}

    def test_nested_braces(self):
        result = parse_json('{"outer": {"inner": [1, 2, 3]}}')
        assert result == {"outer": {"inner": [1, 2, 3]}}

    def test_escaped_quotes_in_strings(self):
        result = parse_json('{"text": "他说\\"你好\\""}')
        assert result == {"text": '他说"你好"'}

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            parse_json("")

    def test_no_json_found_raises(self):
        with pytest.raises(ValueError):
            parse_json("纯文本，没有 JSON")

    def test_trailing_comma_in_object(self):
        result = parse_json('{"a": 1, "b": 2,}')
        assert result == {"a": 1, "b": 2}

    def test_trailing_comma_in_array(self):
        result = parse_json('[1, 2, 3,]')
        assert result == [1, 2, 3]

    def test_truncated_json_auto_close(self):
        result = parse_json('{"a": 1, "b": [1, 2')
        assert result == {"a": 1, "b": [1, 2]}

    def test_truncated_json_with_nested_objects(self):
        result = parse_json('{"outer": {"inner": {"deep": "val"')
        assert result == {"outer": {"inner": {"deep": "val"}}}

    def test_single_quoted_json(self):
        result = parse_json("{'a': 1, 'b': 'hello'}")
        assert result == {"a": 1, "b": "hello"}

    def test_llm_response_with_json_in_middle(self):
        result = parse_json('根据您的需求，以下是结果：\n```json\n{"name": "test"}\n```\n希望对您有帮助')
        assert result == {"name": "test"}
