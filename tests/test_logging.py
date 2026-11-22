import io
import json
import logging
import subprocess
import sys
import pytest

from picoke.logging import JSONFormatter


def make_record(message='hello %s', args=('world',), **kwargs):
    record = logging.LogRecord('test.logger', logging.INFO, 'sample.py', 12, message, args, None)
    record.created = 1_700_000_000.1239
    for name, value in kwargs.items():
        setattr(record, name, value)
    return record


def expected_base(**extra):
    return {
        'timestamp': '2023-11-14T22:13:20.123Z',
        'level': 'INFO',
        'logger': 'test.logger',
        'message': 'hello world',
        **extra,
    }


def test_formats_compact_ascii_json_and_does_not_mutate_record():
    record = make_record()
    before = vars(record).copy()

    output = JSONFormatter().format(record)

    assert output == json.dumps(expected_base(), ensure_ascii=True, separators=(',', ':'))
    assert output.isascii()
    assert not output.endswith('\n')
    assert vars(record) == before


def test_fields_are_optional_but_explicit_empty_mapping_is_retained():
    formatter = JSONFormatter()
    assert json.loads(formatter.format(make_record())) == expected_base()
    empty = make_record(fields={})
    assert json.loads(formatter.format(empty)) == expected_base(fields={})
    assert vars(empty)['fields'] == {}


def test_nested_fields_accept_mappings_lists_and_json_scalars():
    fields = {
        'nested': {'sequence': [None, True, 4, 2.5, '雪']},
        'empty': [],
    }
    parsed = json.loads(JSONFormatter().format(make_record(fields=fields)))
    assert parsed == expected_base(fields=fields)
    assert json.loads(JSONFormatter().format(make_record(fields={'snow': '雪'})))['fields'] == {'snow': '雪'}


@pytest.mark.parametrize('fields', [None, (), {1: 'value'}, {'nested': (1, 2)}, {'nested': {False: 'value'}}, {1, 2}, 1])
def test_fields_reject_invalid_types_and_keys_with_type_error(fields):
    with pytest.raises(TypeError):
        JSONFormatter().format(make_record(fields=fields))


@pytest.mark.parametrize('fields', [{'value': float('nan')}, {'value': float('inf')}, {'nested': {'value': float('-inf')}}])
def test_fields_reject_non_finite_floats(fields):
    with pytest.raises(ValueError):
        JSONFormatter().format(make_record(fields=fields))


def test_fields_reject_cycles_but_accept_shared_acyclic_containers():
    shared = {'value': [1]}
    parsed = json.loads(JSONFormatter().format(make_record(fields={'left': shared, 'right': shared})))
    assert parsed['fields']['left'] == parsed['fields']['right'] == shared

    cyclic = []
    cyclic.append(cyclic)
    with pytest.raises(ValueError):
        JSONFormatter().format(make_record(fields={'cycle': cyclic}))

    cyclic_mapping = {}
    cyclic_mapping['self'] = cyclic_mapping
    with pytest.raises(ValueError):
        JSONFormatter().format(make_record(fields=cyclic_mapping))


def test_fields_are_not_mutated_during_serialization():
    fields = {'nested': [{'value': '雪'}]}
    record = make_record(fields=fields)
    before = repr(fields)
    JSONFormatter().format(record)
    assert repr(fields) == before


def test_record_message_is_materialized_once():
    class Message:
        calls = 0

        def __str__(self):
            self.calls += 1
            return 'once'

    message = Message()
    record = make_record(message, ())
    assert json.loads(JSONFormatter().format(record))['message'] == 'once'
    assert message.calls == 1


def test_exception_and_stack_hooks_are_used_without_exc_text_cache():
    class HookFormatter(JSONFormatter):
        exceptions = 0
        stacks = 0

        def formatException(self, exc_info):
            self.exceptions += 1
            return 'formatted exception'

        def formatStack(self, stack_info):
            self.stacks += 1
            return 'formatted stack'

    formatter = HookFormatter()
    try:
        raise RuntimeError('boom')
    except RuntimeError:
        exc_info = sys.exc_info()

    record = make_record(exc_info=exc_info, stack_info='raw stack', exc_text='stale cache')
    parsed = json.loads(formatter.format(record))
    assert parsed == expected_base(exception='formatted exception', stack='formatted stack')
    assert formatter.exceptions == formatter.stacks == 1
    assert record.exc_text == 'stale cache'


def test_falsy_exception_and_stack_info_are_omitted():
    parsed = json.loads(JSONFormatter().format(make_record(exc_info=None, stack_info=None)))
    assert parsed == expected_base()


def test_formatters_do_not_reuse_another_formatters_exc_text():
    class AlternateFormatter(JSONFormatter):
        def formatException(self, exc_info):
            return 'alternate exception'

    try:
        raise ValueError('real exception')
    except ValueError:
        exc_info = sys.exc_info()
    record = make_record(exc_info=exc_info)

    first = json.loads(JSONFormatter().format(record))
    second = json.loads(AlternateFormatter().format(record))
    assert first['exception'] != second['exception']
    assert 'real exception' in first['exception']
    assert second['exception'] == 'alternate exception'
    assert record.exc_text is None


def test_stdlib_stream_and_file_handlers_emit_json_lines(tmp_path):
    stream = io.StringIO()
    stream_handler = logging.StreamHandler(stream)
    stream_handler.setFormatter(JSONFormatter())
    stream_handler.emit(make_record(fields={'city': '雪'}))
    stream_handler.flush()
    assert stream.getvalue().endswith('\n')
    assert json.loads(stream.getvalue())['fields'] == {'city': '雪'}

    path = tmp_path / 'events.jsonl'
    file_handler = logging.FileHandler(path, encoding='utf-8')
    try:
        file_handler.setFormatter(JSONFormatter())
        file_handler.emit(make_record(fields={'city': '雪'}))
        file_handler.flush()
    finally:
        file_handler.close()
    raw = path.read_bytes()
    assert raw.endswith(b'\n')
    assert b'\\u96ea' in raw
    assert json.loads(raw) == expected_base(fields={'city': '雪'})


def test_dict_config_constructs_formatter_and_handler_in_isolated_process(tmp_path):
    path = tmp_path / 'configured.jsonl'
    code = '''
import json
import logging.config
import sys

path = sys.argv[1]
logging.config.dictConfig({
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {'json': {'()': 'picoke.logging.JSONFormatter'}},
    'handlers': {'file': {
        'class': 'logging.FileHandler', 'filename': path,
        'encoding': 'utf-8', 'formatter': 'json',
    }},
    'loggers': {'test.picoke.logging.dictconfig': {
        'handlers': ['file'], 'level': 'INFO', 'propagate': False,
    }},
})
logging.getLogger('test.picoke.logging.dictconfig').info(
    'city %s', '雪', extra={'fields': {'city': '雪'}}
)
'''
    subprocess.run([sys.executable, '-c', code, str(path)], check=True)
    event = json.loads(path.read_text(encoding='utf-8'))
    assert event['level'] == 'INFO'
    assert event['logger'] == 'test.picoke.logging.dictconfig'
    assert event['message'] == 'city 雪'
    assert event['fields'] == {'city': '雪'}
    assert event['timestamp'].endswith('Z')
