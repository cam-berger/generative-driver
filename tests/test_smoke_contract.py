"""The smoke runs on the one driver format: its stand-in model says what `measure` means, the scripted interpret worker
hands on that model alone, and fresh reuse is served the tool the model renders
(docs/adr/0010-a-package-is-data-in-one-format.md, decision 3)."""
import hashlib
import json
import unittest
from pathlib import Path

from test_service_smoke import SmokeCase


class SmokeFormatTests(SmokeCase):
    def test_02_B12_the_smoke_runs_on_the_one_format_and_reuse_is_served_the_models_description(self):
        from generative_driver.toolkit import resources_root
        code, report = self.smoke(env=self.environment({'keep': True}))
        self.assertEqual((code, report['ok']), (0, True), report)
        [home] = self.homes()
        events = self.events(home)
        # The stand-in model is of the one format and describes `measure` and its temperature. The stand-in device's
        # `T:21.5` states no unit, so the model has none and an uncertainty that says so.
        stand_in = json.loads((resources_root() / 'smoke/model.json').read_text(encoding='utf-8'))
        self.assertEqual(stand_in['schema'], 'interface-model/7')
        measure = stand_in['operations']['measure']
        temperature = measure['outputs']['temperature']
        self.assertTrue(measure['description'].strip() and temperature['description'].strip())
        self.assertNotIn('unit', temperature)
        self.assertEqual([caveat['code'] for caveat in temperature['uncertainties']], ['unit_not_stated'])
        # The scripted interpret worker hands on the model alone.
        [interpreted] = [h for h in self.records(home, 'handoffs') if h['stage'] == 'interpret']
        handed = Path(interpreted['artifacts'][0]['path'])
        self.assertEqual(json.loads((handed / 'model.json').read_text(encoding='utf-8')), stand_in)
        self.assertEqual([path.name for path in handed.rglob('*') if 'contract' in path.name], [])
        # Reuse is served the catalog of the package it was given, and its tool shows what the model says.
        [reuse] = [e['data'] for e in events if e['kind'] == 'stage.assigned' and e['data']['stage'] == 'reuse']
        [package] = reuse['inputs']
        catalog_path = Path(package) / 'mcp-tools.json'
        served = [e['data'] for e in events if e['kind'] == 'mcp.catalog_served']
        self.assertEqual([entry['catalog_sha256'] for entry in served],
                         [hashlib.sha256(catalog_path.read_bytes()).hexdigest()])
        catalog = json.loads(catalog_path.read_text(encoding='utf-8'))
        tool = next(tool for tool in catalog['tools'] if tool['name'] == 'device_measure')
        self.assertEqual(tool['description'], measure['description'])
        shown = tool['outputSchema']['properties']['outputs']['properties']['temperature']['description']
        self.assertEqual(shown.split('\n'), [temperature['description'], 'Unit: not stated by the device.',
                                             'Warning: ' + temperature['uncertainties'][0]['message']])


if __name__ == '__main__':
    unittest.main()
