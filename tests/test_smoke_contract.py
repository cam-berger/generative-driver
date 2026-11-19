"""The smoke's scripted interpret worker writes the driver contract, and its reuse stage is served the catalog that
contract renders."""
import hashlib
import json
import unittest
from pathlib import Path

from test_service_smoke import SmokeCase


class SmokeContractTests(SmokeCase):
    def test_05_B10_the_reuse_catalog_shows_the_workers_description_and_a_unit_the_device_does_not_state(self):
        from generative_driver.toolkit import resources_root
        code, report = self.smoke(env=self.environment({'keep': True}))
        self.assertEqual((code, report['ok']), (0, True), report)
        [home] = self.homes()
        events = self.events(home)
        # The stand-in device's `T:21.5` states no unit, so neither does the stand-in model.
        stand_in = json.loads((resources_root() / 'smoke/model.json').read_text(encoding='utf-8'))
        self.assertNotIn('unit', stand_in['operations']['measure']['outputs']['temperature'])
        # The interpret worker wrote contract.json beside the model, with contract.py in its workspace.
        [interpreted] = [h for h in self.records(home, 'handoffs') if h['stage'] == 'interpret']
        contract = json.loads((Path(interpreted['artifacts'][0]['path']) / 'contract.json').read_text(encoding='utf-8'))
        temperature = contract['operations']['measure']['outputs']['temperature']
        self.assertIsNone(temperature['unit'])
        self.assertEqual([caveat['code'] for caveat in temperature['uncertainties']], ['unit_not_stated'])
        # Reuse is served the catalog of the package it was given.
        [reuse] = [e['data'] for e in events if e['kind'] == 'stage.assigned' and e['data']['stage'] == 'reuse']
        [package] = reuse['inputs']
        catalog_path = Path(package) / 'mcp-tools.json'
        served = [e['data'] for e in events if e['kind'] == 'mcp.catalog_served']
        self.assertEqual([entry['catalog_sha256'] for entry in served],
                         [hashlib.sha256(catalog_path.read_bytes()).hexdigest()])
        catalog = json.loads(catalog_path.read_text(encoding='utf-8'))
        measure = next(tool for tool in catalog['tools'] if tool['name'] == 'device_measure')
        self.assertEqual(measure['description'].split('\n')[0], contract['operations']['measure']['description'])
        self.assertNotEqual(measure['description'].split('\n')[0], 'Execute public operation measure.')
        unit = measure['outputSchema']['properties']['outputs']['properties']['temperature']['description']
        self.assertIn('Unit: not stated by the device.', unit.split('\n'))


if __name__ == '__main__':
    unittest.main()
