import json

import pytest

from regwatch.llm.json_parser import extract_json_array, extract_json_object


def test_bare_object():
    assert extract_json_object('{"a": 1}') == {"a": 1}


def test_fenced_object():
    assert extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}


def test_fenced_object_without_language():
    assert extract_json_object('```\n{"a": 1}\n```') == {"a": 1}


def test_object_with_leading_prose():
    assert extract_json_object('Here is the JSON: {"a": 1}') == {"a": 1}


def test_object_with_trailing_prose():
    assert extract_json_object('{"a": 1}\nHope that helps.') == {"a": 1}


def test_object_raises_on_garbage():
    with pytest.raises(json.JSONDecodeError):
        extract_json_object("definitely not JSON")


def test_bare_array():
    assert extract_json_array('[{"a": 1}, {"b": 2}]') == [{"a": 1}, {"b": 2}]


def test_fenced_array():
    assert extract_json_array('```json\n[1, 2, 3]\n```') == [1, 2, 3]


def test_array_with_prose():
    assert extract_json_array('Sure, here are the items:\n[1, 2]') == [1, 2]


def test_array_raises_on_garbage():
    with pytest.raises(json.JSONDecodeError):
        extract_json_array("no array here")
