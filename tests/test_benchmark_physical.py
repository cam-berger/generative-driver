import tempfile
import unittest
from pathlib import Path
from generative_driver.benchmark import prepare_stage


class PhysicalPrerequisitesTests(unittest.TestCase):
    def test_physical_profile_requests_explicit_binding_before_agent_or_io(self):
        with tempfile.TemporaryDirectory() as directory:
            prepared = prepare_stage('bme280','acquire',Path(directory)/'run',Path(directory)/'worker')
            self.assertIn('blocked',prepared)
            self.assertIn('binding',prepared['blocked'].lower())
            self.assertNotIn('unsupported',prepared['blocked'].lower())

    def test_worker_claim_cannot_supply_its_own_independent_physical_reference(self):
        from generative_driver.benchmark import check_stage
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            prepared=prepare_stage('bme280','ground',root/'run',root/'worker',options={'binding':{'url':'ftdi://selected/1'},'configured_effects':['write']})
            self.assertIn('driver_respond',prepared['blocked'])
            checked=check_stage('bme280','ground',root/'run',root/'worker',
                {'status':'completed','reference':{'temperature':20}},options={'binding':{'url':'ftdi://selected/1'},'configured_effects':['write']})
            self.assertFalse(checked['ok'])
            self.assertIn('Operator reference missing',checked['reason'])
