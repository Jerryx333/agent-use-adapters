"""Offline adapter classification, safety, fallback, and protocol tests."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'adapters'))
import codex_adapter
import claude_adapter


class ClassificationTests(unittest.TestCase):
    def test_classify_status_boundaries_and_rate_vs_planned_limits(self):
        cases = [
            ('tokens used 14013', 'upstream_failed', False),
            ('tokens used 14290 15000 15020 15030', 'upstream_failed', False),
            ('Rate limit exceeded, retry in 20s', 'rate_limited', False),
            ('HTTP 429', 'rate_limited', False),
            ('too many requests', 'rate_limited', False),
            ('per second limit', 'rate_limited', False),
            ('RPM exceeded', 'rate_limited', False),
            ('usage limit reached|1760000000', 'quota_exhausted', False),
            ('usage_limit_reached', 'quota_exhausted', False),
            ('Rate limit reached for requests per minute', 'rate_limited', False),
            ('Claude AI usage limit reached|1760000000', 'quota_exhausted', False),
            ('429 rate limit: daily quota exhausted', 'quota_exhausted', False),
            ('Rate limit: hit your weekly limit', 'quota_exhausted', False),
            ('limit resets tomorrow', 'quota_exhausted', False),
            ('per day maximum', 'quota_exhausted', False),
            ('401 Unauthorized', 'credential_missing', True),
            ('HTTP 401', 'credential_missing', True),
            ('HTTP 500', 'network_failed', False),
            ('HTTP 502', 'network_failed', False),
            ('HTTP 503', 'network_failed', False),
        ]
        for adapter in (codex_adapter, claude_adapter):
            for text, reason, fatal in cases:
                with self.subTest(adapter=adapter.__name__, text=text):
                    self.assertEqual(adapter.classify(1, text), (reason, fatal))


class MainExceptionTests(unittest.TestCase):
    def assert_main_failure(self, adapter, exception, reason, fatal):
        request = io.TextIOWrapper(io.BytesIO(b'{"protocol_version": 1}'))
        output = io.StringIO()
        with mock.patch.object(adapter.sys, 'stdin', request), \
                mock.patch.object(adapter.sys, 'stdout', output), \
                mock.patch.object(adapter.signal, 'signal'), \
                mock.patch.object(adapter, 'generate', side_effect=exception) as generate:
            self.assertEqual(adapter.main(), 1)
        generate.assert_called_once_with({'protocol_version': 1})
        self.assertEqual(json.loads(output.getvalue()), {
            'protocol_version': 1, 'state': 'failed', 'accepted': False,
            'reason': reason, 'fatal': fatal})

    def test_codex_interrupted(self):
        self.assert_main_failure(codex_adapter, KeyboardInterrupt(), 'interrupted', False)

    def test_claude_interrupted(self):
        self.assert_main_failure(claude_adapter, KeyboardInterrupt(), 'interrupted', False)

    def test_codex_invalid_request(self):
        self.assert_main_failure(codex_adapter, ValueError('invalid options'), 'invalid_request', True)

    def test_claude_invalid_request(self):
        self.assert_main_failure(claude_adapter, ValueError('invalid options'), 'invalid_request', True)


class OpenCodeTests(unittest.TestCase):
    def setUp(self):
        import opencode_adapter
        self.adapter = opencode_adapter
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.task = self.root / 'task.txt'
        self.task.write_text('private-task')
        self.request = {'model': 'primary-free', 'timeout': 30, 'options': {},
                        'task_file': str(self.task), 'output': str(self.root / 'output')}
        self.addCleanup(mock.patch.stopall)
        mock.patch.dict(os.environ, {'OPENCODE_BIN': sys.executable}).start()
        self.popen = mock.patch.object(opencode_adapter.subprocess, 'Popen').start()
        self.delete = mock.patch.object(opencode_adapter.subprocess, 'run').start()
        self.kill = mock.patch.object(opencode_adapter.os, 'killpg').start()

    def proc(self, events, returncode=0, stderr=''):
        proc = mock.Mock(pid=12345, returncode=returncode)
        proc.communicate.return_value = ('\n'.join(json.dumps(e) for e in events), stderr)
        return proc

    def status(self):
        return json.loads((self.root / 'output/status.json').read_text())

    def error(self, message, session='ses_error'):
        return {'type': 'error', 'sessionID': session, 'error': {'data': {'message': message}}}

    def test_payment_classification(self):
        for message in ('HTTP 402', 'HTTP 402 Payment Required', 'status: 402',
                        'PAYMENT REQUIRED', 'insufficient balance',
                        'insufficient funds', 'insufficient credit', 'insufficient credits',
                        '余额不足', '请充值', '需要付费', '开通会员', '付费会员',
                        'authentication: Payment Required'):
            with self.subTest(message=message):
                self.assertEqual(self.adapter.classify(message), ('payment_required', False))
        for message, reason in (
                ('You exceeded your current quota, please check your plan and billing details',
                 'quota_exhausted'),
                ('rate limit exceeded; check billing details', 'rate_limited'),
                ('HTTP 429', 'rate_limited'),
                ('billing details', 'upstream_failed'),
                ('error 1402 or 4020 or x402', 'upstream_failed'),
                ('processed 402 tokens; HTTP 4020', 'upstream_failed'),
                ('credit balance: 100 free credits', 'upstream_failed'),
                ('elapsed 402ms', 'upstream_failed'),
                ('free tier can only be used from within opencode: 402', 'free_tier_rejected')):
            with self.subTest(message=message):
                self.assertEqual(self.adapter.classify(message),
                                 (reason, reason == 'free_tier_rejected'))

    def test_payment_failure_takes_precedence_across_models(self):
        self.request['options'] = {'fallback_models': 'next-free,last-free'}
        for messages in (
                ('Payment Required', 'quota exceeded', 'private-upstream'),
                ('private-upstream', 'insufficient credits', 'quota exceeded'),
                ('quota exceeded', 'private-upstream', 'HTTP 402'),
                ('Payment Required', 'private-upstream', 'authentication failed')):
            with self.subTest(messages=messages):
                self.popen.reset_mock()
                self.popen.side_effect = [self.proc([self.error(message)]) for message in messages]
                result = self.adapter.generate(self.request)
                self.assertEqual((result['reason'], result['fatal']), ('payment_required', False))
                self.assertEqual(self.status(), result)
                self.assertNotIn('missing_credential', result)
                self.popen.assert_called()
                self.assertEqual(self.popen.call_count, 3)

    def test_payment_failure_preserves_interrupted_fallback(self):
        self.request['options'] = {'fallback_models': 'next-free,last-free'}
        with mock.patch.object(self.adapter, 'attempt', side_effect=[
                ('', 0, 'payment_required', False),
                ('', 0, 'interrupted', False)]) as attempt:
            result = self.adapter.generate(self.request)
        self.assertEqual((result['reason'], result['fatal']), ('interrupted', False))
        self.assertEqual(self.status(), result)
        self.assertEqual([call.args[1] for call in attempt.call_args_list],
                         ['primary-free', 'next-free'])

    def test_payment_failure_allows_successful_fallback(self):
        self.request['options'] = {'fallback_models': 'next-free'}
        self.popen.side_effect = [self.proc([], returncode=1, stderr='Payment Required'),
                                 self.proc([{'type': 'text', 'part': {'text': 'OK'}}])]
        result = self.adapter.generate(self.request)
        self.assertEqual(result['state'], 'staged_for_review')
        self.assertEqual(result['response'], 'OK')
        self.assertEqual(result['metadata']['served_model'], 'next-free')
        self.assertEqual(self.status()['attempts'][0]['reason'], 'payment_required')
        self.assertEqual(self.popen.call_count, 2)

    def test_success_concat_argv_environment_sessions_and_cleanup(self):
        events = [{'type': 'text', 'sessionID': 'ses_one', 'part': {'text': 'Hello '}},
                  {'type': 'step_start', 'sessionID': 'ses_two'},
                  {'type': 'text', 'sessionID': 'ses_one', 'part': {'text': 'world'}}]
        proc = self.proc(events)
        stdout, stderr = proc.communicate.return_value
        proc.communicate.return_value = ('not json\n[]\n' + stdout, stderr)
        self.popen.return_value = proc
        result = self.adapter.generate(self.request)
        self.assertEqual(result['response'], 'Hello world')
        argv = self.popen.call_args.args[0]
        self.assertEqual(argv[1:], ['run', '--pure', '-m', 'opencode/primary-free', '--format', 'json'])
        kwargs = self.popen.call_args.kwargs
        self.assertTrue(kwargs['start_new_session'])
        self.assertEqual(json.loads(kwargs['env']['OPENCODE_CONFIG_CONTENT']),
                         {'permission': {'*': 'ask'}, 'autoupdate': False, 'share': 'disabled'})
        self.assertEqual(kwargs['env']['AIW_NESTED'], '1')
        self.assertEqual(proc.communicate.call_args.kwargs['input'],
                         self.adapter.PREAMBLE + 'private-task')
        self.assertTrue(self.adapter.PREAMBLE.startswith('[Execution constraints] This is text-only generation:'))
        self.assertTrue(self.adapter.PREAMBLE.endswith('\n\n'))
        self.assertEqual(self.task.read_text(), 'private-task')
        self.assertFalse(Path(kwargs['cwd']).exists())
        self.assertEqual([c.args[0][1:] for c in self.delete.call_args_list],
                         [['session', 'delete', 'ses_one'], ['session', 'delete', 'ses_two']])
        self.assertEqual(self.status()['tool_calls_rejected'], 0)
        self.assertEqual((self.root / 'output/response.txt').read_text(), 'Hello world')

    def test_tool_rejection_discards_intro_and_falls_back_through_dispatcher(self):
        self.request['options'] = {'fallback_models': 'next-free'}
        self.popen.side_effect = [self.proc([
            {'type': 'text', 'part': {'text': '收到，正在评审……'}},
            {'type': 'tool_use'}, {'type': 'step_finish'}]),
            self.proc([{'type': 'text', 'part': {'text': '最终答案'}}])]
        result = self.adapter.generate(self.request)
        self.assertEqual(result['response'], '最终答案')
        steps = result['metadata']['model_attempts']
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0], {'requested_model': 'primary-free', 'state': 'failed',
                                  'reason': 'tool_use_rejected', 'elapsed_s': steps[0]['elapsed_s']})
        self.assertEqual(steps[1], {'requested_model': 'next-free', 'state': 'staged_for_review',
                                  'elapsed_s': steps[1]['elapsed_s']})
        for step in steps:
            self.assertIsInstance(step['elapsed_s'], (int, float))
            self.assertGreaterEqual(step['elapsed_s'], 0)
        self.assertEqual(result['metadata']['model_switch_count'], 1)
        self.assertEqual(self.status()['attempts'][0]['reason'], 'tool_use_rejected')
        self.assertEqual(self.popen.call_count, 2)
        core = Path(os.environ.get('AGENT_USE_HOME') or ROOT.parent / 'agent-use').expanduser()
        if not (core / 'scripts/run_agent.py').is_file():
            return  # Adapter assertions above still run without a local core.
        sys.path.insert(0, str(core / 'scripts'))
        self.addCleanup(sys.path.pop, 0)
        import run_agent
        route = {'model': 'primary-free', 'options': self.request['options'], 'command': ['mock']}
        with mock.patch.object(run_agent, 'invoke', return_value=result), \
                mock.patch.object(run_agent, 'provider_environment', return_value={}):
            record, content, fatal = run_agent.attempt(
                'opencode', self.task, self.root / 'output', 30, None, route)
        self.assertEqual(content, '最终答案')
        self.assertFalse(fatal)
        self.assertEqual(record['state'], 'staged_for_review')
        self.assertEqual(record['model_attempts'], steps)
        self.assertEqual(record['model_switch_count'], 1)

    def test_only_text_after_last_tool_call_is_returned(self):
        for count in (1, 2):
            with self.subTest(tool_calls=count):
                events = []
                for _ in range(count):
                    events.extend([{'type': 'text', 'part': {'text': '收到……'}},
                                   {'type': 'tool_use'}])
                events.append({'type': 'text', 'part': {'text': '最终答案'}})
                self.popen.return_value = self.proc(events)
                result = self.adapter.generate(self.request)
                self.assertEqual(result['response'], '最终答案')
                self.assertEqual((self.root / 'output/response.txt').read_text(), '最终答案')
                self.assertEqual(result['metadata']['tool_calls_rejected'], count)

    def test_tool_rejection_without_fallback_is_nonfatal(self):
        self.popen.return_value = self.proc([
            {'type': 'text', 'part': {'text': '收到……'}},
            {'type': 'tool_use'}, {'type': 'step_finish'},
            {'type': 'text', 'part': {'text': '  \n'}}])
        result = self.adapter.generate(self.request)
        self.assertEqual(result['reason'], 'tool_use_rejected')
        self.assertFalse(result['fatal'])
        self.assertFalse((self.root / 'output/response.txt').exists())

    def test_invalid_models_and_options(self):
        for values in ({'model': 'paid'}, {'model': 'opencode/primary-free'},
                       {'options': {'fallback_models': 'paid'}},
                       {'options': {'fallback_models': []}}, {'options': {'extra': True}}):
            with self.subTest(values=values):
                result = self.adapter.generate({**self.request, **values})
                self.assertEqual((result['reason'], result['fatal']), ('invalid_request', True))
        self.popen.assert_not_called()

    def test_fallback_skips_explicit_primary_model(self):
        self.request['model'] = 'mimo-v2.6-flash-free'
        self.request['options'] = {
            'fallback_models': 'mimo-v2.6-flash-free,longcat-2.5-preview-free'}
        self.popen.side_effect = [self.proc([self.error('private-upstream')]),
                                 self.proc([{'type': 'text', 'part': {'text': 'OK'}}])]
        result = self.adapter.generate(self.request)
        self.assertEqual(result['metadata']['served_model'], 'longcat-2.5-preview-free')
        self.assertEqual([(a['model'], a['reason']) for a in self.status()['attempts']],
                         [('mimo-v2.6-flash-free', 'upstream_failed'),
                          ('longcat-2.5-preview-free', 'ok')])
        self.assertEqual(self.popen.call_count, 2)
        self.assertEqual([c.args[0][4] for c in self.popen.call_args_list],
                         ['opencode/mimo-v2.6-flash-free', 'opencode/longcat-2.5-preview-free'])

    def test_recoverable_failure_fallback_and_sanitized_status(self):
        self.request['options'] = {'fallback_models': 'next-free,next-free'}
        self.popen.side_effect = [self.proc([self.error('private-upstream')]),
                                 self.proc([{'type': 'text', 'part': {'text': 'OK'}}])]
        result = self.adapter.generate(self.request)
        self.assertEqual(result['metadata']['served_model'], 'next-free')
        self.assertEqual(result['metadata']['actual_model'], 'next-free')
        status = self.status()
        self.assertEqual(status['requested_model'], 'primary-free')
        self.assertEqual(status['served_model'], 'next-free')
        self.assertEqual(status['actual_model'], 'next-free')
        self.assertEqual([(a['model'], a['reason']) for a in status['attempts']],
                         [('primary-free', 'upstream_failed'), ('next-free', 'ok')])
        self.delete.assert_called_once()
        self.assertNotIn('private-upstream', json.dumps(status))

    def test_fatal_free_tier_and_failed_status_no_raw_text(self):
        self.request['options'] = {'fallback_models': 'next-free'}
        self.popen.return_value = self.proc([self.error("OpenCode's free tier can only be used from within OpenCode private-upstream")])
        result = self.adapter.generate(self.request)
        self.assertEqual((result['reason'], result['fatal']), ('free_tier_rejected', True))
        self.popen.assert_called_once()
        self.delete.assert_called_once()
        self.assertEqual(set(self.status()), {'protocol_version', 'state', 'accepted', 'reason', 'fatal'})
        self.assertNotIn('private', json.dumps(self.status()))

    def test_timeout_and_interrupt_kill_group_delete_sessions(self):
        import subprocess
        import signal
        for exception, reason in [(subprocess.TimeoutExpired('opencode', 30), 'timeout'),
                                  (KeyboardInterrupt(), 'interrupted')]:
            with self.subTest(reason=reason):
                proc = self.proc([])
                proc.communicate.side_effect = [exception, subprocess.TimeoutExpired('opencode', 2),
                                               (json.dumps({'type': 'step_start', 'sessionID': 'ses_partial'}), '')]
                self.popen.return_value = proc
                self.kill.reset_mock()
                self.delete.reset_mock()
                result = self.adapter.generate(self.request)
                self.assertEqual(result['reason'], reason)
                self.assertEqual(self.kill.call_args_list,
                                 [mock.call(12345, signal.SIGTERM), mock.call(12345, signal.SIGKILL)])
                self.delete.assert_called_once()
                self.assertEqual(self.delete.call_args.args[0][-1], 'ses_partial')
                self.assertFalse(Path(self.popen.call_args.kwargs['cwd']).exists())

    def test_stop_process_kills_group_after_immediate_communicate(self):
        import signal
        for error in (None, ProcessLookupError(), PermissionError()):
            with self.subTest(error=error):
                proc = self.proc([])
                self.kill.reset_mock()
                self.kill.side_effect = [None, error]
                self.assertEqual(self.adapter.stop_process(proc), ('', ''))
                proc.communicate.assert_called_once_with(timeout=2)
                self.assertEqual(self.kill.call_args_list,
                                 [mock.call(12345, signal.SIGTERM), mock.call(12345, signal.SIGKILL)])

    def test_delete_interrupt_removes_directory_and_returns_interrupted(self):
        self.request['options'] = {'fallback_models': 'next-free'}
        self.popen.return_value = self.proc([
            {'type': 'text', 'sessionID': 'ses_one', 'part': {'text': 'OK'}},
            {'type': 'step_start', 'sessionID': 'ses_two'}])
        self.delete.side_effect = KeyboardInterrupt
        result = self.adapter.generate(self.request)
        self.assertEqual(result['reason'], 'interrupted')
        self.assertFalse(result['fatal'])
        self.assertEqual(self.status()['reason'], 'interrupted')
        self.delete.assert_called_once()
        self.popen.assert_called_once()
        self.assertFalse(Path(self.popen.call_args.kwargs['cwd']).exists())

    def test_session_cleanup_uses_remaining_budget(self):
        events = '\n'.join(json.dumps({'sessionID': f'ses_{i}'}) for i in range(4))
        for elapsed in (9.5, 10):
            with self.subTest(elapsed=elapsed):
                workdir = self.root / 'cleanup'
                workdir.mkdir()
                self.delete.reset_mock()
                with mock.patch.object(self.adapter.time, 'monotonic',
                                       side_effect=[0, 0, 6, elapsed]):
                    self.assertFalse(self.adapter.cleanup_sessions('opencode', events, {}, workdir))
                self.assertEqual([call.kwargs['timeout'] for call in self.delete.call_args_list], [10, 4])
                self.assertFalse(workdir.exists())

    def test_cleanup_interrupt_does_not_swallow_original_exception(self):
        workdir = self.root / 'cleanup'
        workdir.mkdir()
        with mock.patch.object(self.adapter.tempfile, 'mkdtemp', return_value=str(workdir)), \
                mock.patch.object(self.adapter, 'parse_events', side_effect=[ValueError('original'),
                                                                          ('', 0, {'ses_one'}, None)]):
            self.popen.return_value = self.proc([])
            self.delete.side_effect = KeyboardInterrupt
            with self.assertRaisesRegex(ValueError, 'original'):
                self.adapter.attempt('opencode', 'primary-free', 'task', self.root, {}, 30)
        self.assertFalse(workdir.exists())

    def test_shared_budget_prevents_retry_under_five_seconds(self):
        self.request['options'] = {'fallback_models': 'next-free'}
        self.popen.return_value = self.proc([self.error('upstream')])
        with mock.patch.object(self.adapter.time, 'monotonic', side_effect=[0, 0, 0, 26, 26, 26, 26]):
            result = self.adapter.generate(self.request)
        self.assertEqual(result['reason'], 'upstream_failed')
        self.popen.assert_called_once()
        self.assertEqual(self.popen.return_value.communicate.call_args.kwargs['timeout'], 30)

    def test_empty_nonzero_auth_and_cleanup_failure(self):
        import subprocess
        for events, code, stderr, reason in [([], 0, '', 'empty_response'),
                ([], 1, 'private-upstream', 'upstream_failed'),
                ([self.error('HTTP 401 private-upstream')], 0, '', 'credential_missing')]:
            with self.subTest(reason=reason):
                self.popen.return_value = self.proc(events, code, stderr)
                self.delete.side_effect = subprocess.TimeoutExpired('delete', 5)
                result = self.adapter.generate(self.request)
                self.assertEqual(result['reason'], reason)
                self.assertNotIn('private', json.dumps(self.status()))

    def test_missing_binary(self):
        with mock.patch.dict(os.environ, {'OPENCODE_BIN': str(self.root / 'missing')}):
            result = self.adapter.generate(self.request)
        self.assertEqual(result['missing_credential'], 'opencode-cli')
        self.assertTrue(result['fatal'])
        self.popen.assert_not_called()


class ProtocolTests(unittest.TestCase):
    def invoke(self, name, root, binary):
        task = root / 'task.txt'
        task.write_text('Write a short answer.', encoding='utf-8')
        output = root / 'output'
        request = {'protocol_version': 1, 'task_file': str(task),
                   'output': str(output), 'timeout': 10, 'options': {}}
        env = {**os.environ, name.upper() + '_BIN': str(binary)}
        result = subprocess.run([sys.executable, '-B', str(ROOT / 'adapters' / (name + '_adapter.py'))],
                                input=json.dumps(request), text=True, capture_output=True,
                                env=env, timeout=15)
        return result, json.loads(result.stdout), output

    def test_fake_cli_success_for_each_adapter(self):
        for name in ('codex', 'claude', 'opencode'):
            with self.subTest(adapter=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                binary = root / 'fake-cli'
                binary.write_text('#!' + sys.executable + '\n' + '''import json
import os
from pathlib import Path
import sys
assert os.environ['AIW_NESTED'] == '1'
if sys.argv[1:3] == ['session', 'delete']:
    sys.exit(0)
assert 'Write a short answer.' in sys.stdin.read()
if '-o' in sys.argv:
    Path(sys.argv[sys.argv.index('-o') + 1]).write_text('Offline answer')
elif '--format' in sys.argv:
    print(json.dumps({'type': 'text', 'sessionID': 'ses_offline', 'part': {'text': 'Offline answer'}}))
else:
    print('Offline answer', end='')
''', encoding='utf-8')
                binary.chmod(0o700)
                process, reply, output = self.invoke(name, root, binary)
                self.assertEqual(process.returncode, 0, process.stderr)
                self.assertEqual(reply['protocol_version'], 1)
                self.assertEqual(reply['state'], 'staged_for_review')
                self.assertIs(reply['accepted'], False)
                self.assertEqual(reply['response'], 'Offline answer')
                self.assertEqual((output / 'response.txt').read_text(), 'Offline answer')
                status = json.loads((output / 'status.json').read_text())
                self.assertEqual(status['state'], 'staged_for_review')
                self.assertIs(status['accepted'], False)
                self.assertFalse(any(output.glob('*-adapter-*')))

    def test_missing_cli_for_each_adapter(self):
        for name in ('codex', 'claude', 'opencode'):
            with self.subTest(adapter=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                process, reply, _ = self.invoke(name, root, root / 'nonexistent-cli')
                self.assertEqual(process.returncode, 1)
                self.assertEqual(reply['state'], 'failed')
                self.assertIs(reply['accepted'], False)
                self.assertEqual(reply['reason'], 'credential_missing')
                self.assertIs(reply['fatal'], True)
