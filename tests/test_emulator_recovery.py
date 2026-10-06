"""Trusted native-owner boundary, using external process substitutes only."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class Owner:
    def __init__(self, port, process=None):
        self.recipe={'observations':{'counter':{'width':32,'count':1,'command':'counter'}}}
        self.info={'binding':{'host':'127.0.0.1','port':port},'session_id':'old'}
        self.process=process or Process()
        self.value=0
    @property
    def binding(self):return self.info['binding']
    def observe(self):
        return {'values':{'counter':self.value},'reads':[{'name':'counter','value':self.value,'command':'counter','response':str(self.value)}]}
    def stop(self):self.process.stopped=True


class Process:
    stopped=False
    def poll(self):return 0 if self.stopped else None


class EmulatorRecoveryTests(unittest.TestCase):
    def test_startup_values_require_matching_independent_read_evidence(self):
        from generative_driver.benchmark_support.emulated_recovery import observed_values
        owner=Owner(1)
        owner.observe=lambda:{'values':{'counter':0},'reads':[]}
        with self.assertRaisesRegex(RuntimeError,'observation'):
            observed_values(owner)

    def test_new_session_cannot_reuse_old_process_handle(self):
        from generative_driver.benchmark_support import emulated
        from generative_driver.benchmark_support.cases import _state,_write
        from generative_driver.benchmark_support.emulated_recovery import replace_owned
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);old=Owner(1);new=Owner(2,old.process)
            path,state=_state(root);state.update(session=old.info,startup_observation=old.observe());_write(path,state)
            key=str(root.resolve());emulated._OWNERS[key]=old
            # A launcher returning a formerly stopped handle is not trusted ownership.
            def start(**kwargs):old.process.stopped=False;return new
            try:
                with patch.object(emulated,'_inputs',return_value=(root,{})),patch.object(emulated,'_truth',return_value={'recipe':old.recipe}),patch('generative_driver.benchmark_support.native.NativeSession.start',side_effect=start):
                    with self.assertRaisesRegex(RuntimeError,'distinct'):
                        replace_owned('synthetic',root,{},lambda:False)
                self.assertTrue(new.process.stopped)
            finally:emulated._OWNERS.pop(key,None)

    def test_cleanup_retains_owned_handle_when_stop_is_unconfirmed(self):
        from generative_driver.benchmark_support import emulated
        from generative_driver.benchmark_support.cases import _state,_write
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);owner=Owner(1);owner.stop=lambda:None
            path,state=_state(root);state['session']=owner.info;_write(path,state)
            key=str(root.resolve());emulated._OWNERS[key]=owner
            try:
                with self.assertRaisesRegex(RuntimeError,'stop'):
                    emulated.cleanup('synthetic',root)
                self.assertIs(emulated._OWNERS[key],owner)
            finally:emulated._OWNERS.pop(key,None)

    def test_failed_replacement_retains_handle_if_its_stop_raises(self):
        from generative_driver.benchmark_support import emulated
        from generative_driver.benchmark_support.cases import _state,_write
        from generative_driver.benchmark_support.emulated_recovery import replace_owned
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);old=Owner(1);new=Owner(2);new.value=1
            def stop():raise RuntimeError('stop unavailable')
            new.stop=stop
            path,state=_state(root);state.update(session=old.info,startup_observation=old.observe());_write(path,state)
            key=str(root.resolve());emulated._OWNERS[key]=old
            try:
                with patch.object(emulated,'_inputs',return_value=(root,{})),patch.object(emulated,'_truth',return_value={'recipe':old.recipe}),patch('generative_driver.benchmark_support.native.NativeSession.start',return_value=new):
                    with self.assertRaises(RuntimeError):replace_owned('synthetic',root,{},lambda:False)
                self.assertIs(emulated._OWNERS.get(key),new)
            finally:emulated._OWNERS.pop(key,None)

    def test_initial_observation_failure_retains_live_owner_when_stop_fails(self):
        from generative_driver.benchmark_support import emulated
        from generative_driver.benchmark_support.cases import _state
        for raises in (True,False):
            with self.subTest(stop_raises=raises),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);owner=Owner(1)
                owner.observe=lambda:{'values':{},'reads':[]}
                def stop():
                    if raises:raise RuntimeError('stop unavailable')
                owner.stop=stop
                key=str(root.resolve())
                try:
                    with patch.object(emulated,'_inputs',return_value=(root,{})),patch.object(emulated,'_truth',return_value={'recipe':owner.recipe}),patch('generative_driver.benchmark_support.native.NativeSession.start',return_value=owner):
                        with self.assertRaises(RuntimeError):emulated._session('synthetic',root,{})
                    self.assertIs(emulated._OWNERS.get(key),owner)
                    self.assertEqual(_state(root)[1]['session']['session_id'],owner.info['session_id'])
                    self.assertIsNone(owner.process.poll())
                    with self.assertRaisesRegex(RuntimeError,'stop'):emulated.cleanup('synthetic',root)
                    self.assertIs(emulated._OWNERS.get(key),owner)
                finally:emulated._OWNERS.pop(key,None)
