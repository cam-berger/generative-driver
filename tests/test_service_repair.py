"""Scripted model defects exercise case routing without inference or an emulator."""
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
            from suite_fixtures import write_scripted_case_worker
            command=write_scripted_case_worker(home,hold_on_repair=True)
            script=Path(command[1])
            try:
                run=call('start',{'goal':'Scripted revision contract, not a benchmark result','case':'tq9',
                    'executor_config':{'command':[sys.executable,str(script)]}},home)
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
