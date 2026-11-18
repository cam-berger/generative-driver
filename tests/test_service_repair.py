"""Scripted model defects exercise case routing without inference or an emulator."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest



class RepairServiceTests(unittest.TestCase):
    def test_old_gateway_is_invalidated_when_a_model_defect_starts_a_new_revision(self):
        from generative_driver.client import call
        with tempfile.TemporaryDirectory() as home:
            from supplied_device import SuppliedDevice
            from test_supplied_stages import MISSION, hand_written_firmware
            from worker_script import write_scripted_worker
            # The first interpretation is one the device's TCP binding cannot carry: probe skips, and the
            # configurator's model diff starts a new revision with no device.
            command=write_scripted_worker(home,hold_on_repair=True)
            script=Path(command[1])
            device=SuppliedDevice(Path(home)/'device')
            self.addCleanup(device.close)
            try:
                # The run starts on the hand-written device, firmware and mission, as a client supplies them.
                run=call('start',{'goal':'Scripted revision contract, not a benchmark result',
                    'device':device.descriptor,'firmware':hand_written_firmware(Path(home)/'fixture'),
                    'mission':MISSION,'evaluation_token_sha256':hashlib.sha256(b'token').hexdigest(),
                    'effects':['write','actuate'],'executor_config':{'command':[sys.executable,str(script)]}},home)
                assignments=[]
                # Wait for each durable transition, not a total runtime guess
                # spanning several external workers and their case checks.
                for count,stage in enumerate(('acquire','interpret','interpret'),1):
                    until=time.monotonic()+10
                    while True:
                        events=call('events',run,home)['events']
                        assignments=[e['data'] for e in events if e['kind']=='stage.assigned']
                        if len(assignments)>=count: break
                        state=call('status',run,home)
                        if state['status'] in ('failed','blocked','cancelled','completed') or time.monotonic()>=until:
                            self.fail(f'Waiting for assignment {count} ({stage}); '
                                      f'last event: {events[-1] if events else None}; '
                                      f'result: {call("result",run,home)}')
                        time.sleep(.03)
                self.assertEqual([a['stage'] for a in assignments[:3]],['acquire','interpret','interpret'],call('result',run,home))
                old=call('tools',{**run,'assignment_id':assignments[1]['id']},home)
                self.assertFalse(old['ok'])
                self.assertIn('inactive',old['reason'])
                result=call('result',run,home)
                self.assertEqual(result['progress']['repairs'],1)
                self.assertNotIn('maintenance_cycles',result['progress'])
                self.assertEqual(result['progress']['revision'],1)
            finally:
                call('cancel',run,home)
                call('shutdown',{},home)


if __name__=='__main__':
    unittest.main()
