"""Validate the bundled registry, optionally against a local agent-use core."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / 'config/dispatcher.json'


class RegistryTests(unittest.TestCase):
    def test_registry_structure_and_commands(self):
        config = json.loads(REGISTRY.read_text(encoding='utf-8'))
        self.assertEqual(config['version'], 1)
        providers = config['providers']
        self.assertEqual(set(providers), {'codex', 'claude', 'opencode', 'opencode-acp', 'qwen-acp'})
        self.assertEqual(config['default_profile'], 'default')
        self.assertEqual(config['profiles']['default'], ['codex', 'claude', 'opencode'])
        for name, provider in providers.items():
            adapter = 'acp' if name.endswith('-acp') else name
            self.assertEqual(provider['command'], [
                '{python}', '-B', '{config_dir}/../adapters/' + adapter + '_adapter.py'])
            command = provider['command'][2].replace('{config_dir}', str(REGISTRY.parent))
            self.assertTrue(Path(command).is_file())
            if name.endswith('-acp'):
                self.assertEqual(provider['options'], {'agent': name.removesuffix('-acp')})
                self.assertEqual(provider['tool_policy'], 'workspace')
                self.assertEqual(provider['env_names'], ['ACP_' + name.removesuffix('-acp').upper() + '_BIN'])
                self.assertNotIn(name, config['profiles']['default'])
            else:
                self.assertEqual(config['profiles'][name + '-only'], [name])
        for profile in config['profiles'].values():
            self.assertTrue(profile)
            self.assertTrue(set(profile) <= set(providers))

    def test_core_loads_registry(self):
        core = Path(os.environ.get('AGENT_USE_HOME') or ROOT.parent / 'agent-use').expanduser()
        entry = core / 'scripts/agent_use.py'
        if not entry.is_file():
            self.skipTest('Local agent-use core not found')
        result = subprocess.run([sys.executable, '-B', str(entry), 'list', '--config', str(REGISTRY)],
                                text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        catalog = json.loads(result.stdout)
        self.assertEqual({p['provider'] for p in catalog['providers']},
                         {'codex', 'claude', 'opencode', 'opencode-acp', 'qwen-acp'})
