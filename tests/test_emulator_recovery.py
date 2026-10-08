"""Trusted native-owner boundary through the device provider, using external process substitutes only."""
import hashlib
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
    def set_running(self,running):pass
    def stop(self):self.process.stopped=True


class Process:
    stopped=False
    def poll(self):return 0 if self.stopped else None


def provider(root,launches):
    """A provider of a synthetic image whose launches return the given owners in order (a callable launches one)."""
    from generative_driver.benchmark_support.device_provider import DeviceProvider
    image=root/'firmware.bin';image.write_bytes(b'synthetic')
    return DeviceProvider('tq9-v2',image,Owner(0).recipe,None,root/'device',start=lambda **kwargs:(lambda item:item() if callable(item) else item)(launches.pop(0)))


class EmulatorRecoveryTests(unittest.TestCase):
    def test_startup_values_require_matching_independent_read_evidence(self):
        from generative_driver.benchmark_support.device_provider import observed_values
        owner=Owner(1)
        owner.observe=lambda:{'values':{'counter':0},'reads':[]}
        with self.assertRaisesRegex(RuntimeError,'observation'):
            observed_values(owner)

    def test_new_session_cannot_reuse_old_process_handle(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);old=Owner(1);new=Owner(2,old.process)
            # A launcher returning a formerly stopped handle is not trusted ownership.
            def start():old.process.stopped=False;return new
            device=provider(root,[old,start]);session_id=device.start()['session_id']
            with self.assertRaisesRegex(RuntimeError,'distinct'):
                device.replace(session_id,[],[],lambda:False)
            self.assertTrue(new.process.stopped)

    def test_cleanup_retains_owned_handle_when_stop_is_unconfirmed(self):
        from generative_driver.benchmark_support import emulated
        from generative_driver.benchmark_support.cases import _state,_write
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);owner=Owner(1);device=provider(root,[owner]);device.start();owner.stop=lambda:None
            path,state=_state(root);state['session']=device.descriptor();_write(path,state)
            key=str(root.resolve());emulated._OWNERS[key]=device
            try:
                with self.assertRaisesRegex(RuntimeError,'stop'):
                    emulated.cleanup('tq9-v2',root)
                self.assertIs(emulated._OWNERS[key],device);self.assertIs(device.session,owner)
            finally:emulated._OWNERS.pop(key,None)

    def test_failed_replacement_retains_handle_if_its_stop_raises(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);old=Owner(1);new=Owner(2);new.value=1
            def stop():raise RuntimeError('stop unavailable')
            new.stop=stop
            device=provider(root,[old,new]);session_id=device.start()['session_id']
            with self.assertRaises(RuntimeError):device.replace(session_id,[],[],lambda:False)
            self.assertIs(device.session,new)

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
                (root/'firmware.bin').write_bytes(b'synthetic')
                manifest={'images':{'firmware.bin':hashlib.sha256(b'synthetic').hexdigest()}}
                try:
                    with patch.object(emulated,'_inputs',return_value=(root,manifest)),patch.object(emulated,'_truth',return_value={'recipe':owner.recipe}),patch('generative_driver.benchmark_support.native.NativeSession.start',return_value=owner),patch.object(emulated,'_device_dir',return_value=root/'device'):
                        with self.assertRaises(RuntimeError):emulated._session('tq9-v2',root,{})
                    self.assertIs(emulated._OWNERS.get(key).session,owner)
                    self.assertEqual(_state(root)[1]['session']['session_id'],owner.info['session_id'])
                    self.assertIsNone(owner.process.poll())
                    with self.assertRaisesRegex(RuntimeError,'stop'):emulated.cleanup('tq9-v2',root)
                    self.assertIs(emulated._OWNERS.get(key).session,owner)
                finally:emulated._OWNERS.pop(key,None)


if __name__ == '__main__':
    unittest.main()
