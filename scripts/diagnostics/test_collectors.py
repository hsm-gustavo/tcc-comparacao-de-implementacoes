"""Collector protocol tests; subprocesses, clocks and signals are mocked."""
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch


class ChunkedBytes(io.BytesIO):
    def read1(self, size=-1):
        return super().read1(min(size, 7))


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CollectorsTest(unittest.TestCase):
    def test_postgres_uses_one_read_only_session_and_separate_jsonl_files(self):
        collector = load('collect-postgres')
        child = MagicMock()
        sample = {'sampled_at': '2026-10-16T12:00:00Z', 'activity': [],
                  'database': {'xact_commit': 12}, 'wal': {'wal_bytes': 123},
                  'bgwriter': {'buffers_checkpoint': 2}}
        child.stdout = io.TextIOWrapper(io.BytesIO(
            (json.dumps({'readOnly': 'on', 'version': 'fake'}) + '\0'
             + json.dumps(sample) + '\0').encode()))
        with tempfile.TemporaryDirectory() as folder:
            with patch('sys.argv', ['collector', '--local', '--output-dir', folder, '--run-id', 'test']), \
                 patch.object(collector.shutil, 'which', return_value='/fake/psql'), \
                 patch.object(collector.subprocess, 'Popen', return_value=child) as spawn, \
                 patch.object(collector.signal, 'signal'), \
                 patch.object(collector.time, 'sleep', side_effect=KeyboardInterrupt):
                collector.main()
            spawn.assert_called_once()
            args, kwargs = spawn.call_args
            self.assertEqual(args[0][0], 'psql')
            self.assertIn('-0', args[0])
            self.assertIn('default_transaction_read_only=on', kwargs['env']['PGOPTIONS'])
            sent_sql = ''.join(call.args[0] for call in child.stdin.write.call_args_list)
            self.assertIn('pid <> pg_backend_pid()', sent_sql)
            self.assertIn('left(query, 200)', sent_sql)
            for name in ['activity', 'database', 'wal', 'bgwriter']:
                record = json.loads((Path(folder) / f'postgres-{name}.jsonl').read_text())
                self.assertEqual(record['runId'], 'test')
                self.assertEqual(record['data'], sample[name])
            child.terminate.assert_called_once()

    def test_postgres_reads_multiline_sessions_and_stops_after_requested_samples(self):
        collector = load('collect-postgres')
        child = MagicMock()
        sample = {'sampled_at': '2026-10-16T12:00:00Z',
                  'activity': [{'pid': 10, 'query': 'SELECT \'ação\\n\"\''},
                               {'pid': 11, 'query': 'COMMIT'}],
                  'database': {}, 'wal': {}, 'bgwriter': {}}
        # PostgreSQL json_agg(record) adds physical whitespace between records.
        # Include fragmented reads, UTF-8 and escaped characters inside SQL text.
        payload = json.dumps(sample, ensure_ascii=False).replace('}, {', '},\n {')
        self.assertIn('\n', payload)
        with self.assertRaises(json.JSONDecodeError):
            json.loads(io.StringIO(payload).readline())
        child.stdout = io.TextIOWrapper(ChunkedBytes(
            ('{"readOnly":"on"}\0' + payload + '\0' + payload + '\0').encode()))
        with tempfile.TemporaryDirectory() as folder:
            with patch('sys.argv', ['collector', '--local', '--output-dir', folder, '--samples', '2']), \
                 patch.object(collector.shutil, 'which', return_value='/fake/psql'), \
                 patch.object(collector.subprocess, 'Popen', return_value=child) as spawn, \
                 patch.object(collector.signal, 'signal'), patch.object(collector.time, 'sleep') as sleep:
                collector.main()
            spawn.assert_called_once()
            sleep.assert_called_once()
            for name in ['activity', 'database', 'wal', 'bgwriter']:
                records = [json.loads(line) for line in (Path(folder) / f'postgres-{name}.jsonl').read_text().splitlines()]
                self.assertEqual(len(records), 2)
                self.assertEqual(records[0]['data'], sample[name])
                self.assertEqual(records[1]['data'], sample[name])

    def test_postgres_frames_large_records_and_preserves_the_next_record(self):
        collector = load('collect-postgres')
        large = {'text': 'x' * 70000, 'unicode': 'ação', 'delimiter': '\0'}
        wire = (json.dumps(large, ensure_ascii=False) + '\0{"next":true}\0').encode()
        records = collector.json_records(io.BytesIO(wire))
        self.assertEqual(next(records), large)
        self.assertEqual(next(records), {'next': True})
        with self.assertRaisesRegex(RuntimeError, 'complete NUL-delimited'):
            next(records)

    def test_postgres_rejects_truncated_or_invalid_records(self):
        collector = load('collect-postgres')
        with self.assertRaisesRegex(RuntimeError, 'complete NUL-delimited'):
            next(collector.json_records(ChunkedBytes(b'{"incomplete":')))
        with self.assertRaises(json.JSONDecodeError):
            next(collector.json_records(ChunkedBytes(b'not-json\0')))

    def test_host_handles_terminal_codes_and_keeps_native_tools_after_docker_stops(self):
        collector = load('collect-host')
        for stream, expected_count, expected_status in [
            ('\x1b[2J\x1b[H\n\n\x1b[H{"Name":"order-db","CPUPerc":"1.0%"}\x1b[K\n'
             '\x1b[J\x1b[H{"Name":"order-service"}\x1b[K\n\x1b[J', 2, 'stopped:'),
            ('unparseable output\n', 0, 'failed:'),
        ]:
            with self.subTest(status=expected_status), tempfile.TemporaryDirectory() as folder:
                native, docker = MagicMock(), MagicMock()
                native.poll.return_value = None
                docker.poll.return_value = 0
                docker.stdout = io.StringIO(stream)
                listed = MagicMock(returncode=0, stdout='fake-container-id\n')
                with patch('sys.argv', ['collector', '--compose', '/unused', '--output-dir', folder]), \
                     patch.object(collector.shutil, 'which', side_effect=lambda tool: '/fake/' + tool if tool in ['vmstat', 'docker'] else None), \
                     patch.object(collector.subprocess, 'run', return_value=listed), \
                     patch.object(collector.subprocess, 'Popen', side_effect=[native, docker]), \
                     patch.object(collector.signal, 'signal'), patch('sys.stdout', new_callable=io.StringIO), \
                     patch.object(collector.time, 'sleep', side_effect=KeyboardInterrupt) as sleep:
                    collector.main()
                sleep.assert_called_once()  # Native collection continued after stream EOF/failure.
                native.terminate.assert_called_once()
                status = json.loads((Path(folder) / 'host-collector-status.json').read_text())
                self.assertTrue(status['docker'].startswith(expected_status))
                records = [json.loads(line) for line in (Path(folder) / 'containers.jsonl').read_text().splitlines()]
                self.assertEqual(len(records), expected_count)
                if records:
                    self.assertEqual(records[0]['container']['Name'], 'order-db')
                else:
                    docker.terminate.assert_called_once()

    def test_optional_host_tools_are_explicitly_disabled_when_absent(self):
        collector = load('collect-host')
        with tempfile.TemporaryDirectory() as folder:
            with patch('sys.argv', ['collector', '--compose', '/unused', '--output-dir', folder]), \
                 patch.object(collector.shutil, 'which', return_value=None), \
                 patch.object(collector.subprocess, 'Popen') as spawn, \
                 patch.object(collector.signal, 'signal'), patch('sys.stdout', new_callable=io.StringIO):
                collector.main()
            spawn.assert_not_called()
            status = json.loads((Path(folder) / 'host-collector-status.json').read_text())
            self.assertEqual(status, dict.fromkeys(['vmstat', 'pidstat', 'docker'], 'unavailable'))


if __name__ == '__main__':
    unittest.main()
