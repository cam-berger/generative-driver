"""Explicit native gate. No configuration means failure, never a skipped qualification."""
import os
import tempfile
import unittest
from pathlib import Path
from generative_driver.benchmark_support.calibration import calibrate

class NativeFamilyCalibrationTests(unittest.TestCase):
    def test_both_authored_families(self):
        required=('GD_FAMILY_AUTHORING','GD_NATIVE_RENODE','GD_NATIVE_GHIDRA_HOME','GD_NATIVE_JAVA_HOME','ARM_NONE_EABI_GCC')
        paths={}
        for name in required:
            self.assertIn(name,os.environ,'Native gate requires explicit '+name)
            paths[name]=Path(os.environ[name])
            self.assertTrue(paths[name].is_absolute() and paths[name].exists(),name)
        for case_id in ('sampled-sensor-v1','parameter-store-v1'):
            with self.subTest(case=case_id):
                output=Path(tempfile.mkdtemp(prefix=case_id+'-calibration-',dir=paths['GD_FAMILY_AUTHORING']))
                result=calibrate(case_id,authoring_root=paths['GD_FAMILY_AUTHORING']/(case_id+'-authoring'),
                    renode=paths['GD_NATIVE_RENODE'],ghidra_home=paths['GD_NATIVE_GHIDRA_HOME'],
                    java_home=paths['GD_NATIVE_JAVA_HOME'],output_dir=output)
                self.assertTrue(result['ok'],result.get('failures'))
                self.assertEqual(result['execution'],'reference-calibration')

    def test_reset_vector_refuses_non_thumb_and_unmapped_entries(self):
        import json,subprocess,sys
        from generative_driver.toolkit import resources_root
        for name in ('GD_NATIVE_GHIDRA_HOME','GD_NATIVE_JAVA_HOME'):
            self.assertIn(name,os.environ,'Native gate requires explicit '+name)
        resources=resources_root()
        for vector in (0x08000008,0x09000001):
            with self.subTest(vector=hex(vector)),tempfile.TemporaryDirectory(prefix='gd-vector-test-') as temp:
                root=Path(temp);binary=root/'toy.bin'
                binary.write_bytes((0x20000100).to_bytes(4,'little')+vector.to_bytes(4,'little')+b'\x00'*8)
                completed=subprocess.run([sys.executable,str(resources/'tools/workspace/ghidra_run.py'),
                    '--workspace',str(root/'analysis'),'--image',str(binary),'--base-address','0x08000000',
                    '--processor','ARM:LE:32:Cortex','--ghidra-path',os.environ['GD_NATIVE_GHIDRA_HOME'],
                    '--java-home',os.environ['GD_NATIVE_JAVA_HOME'],'--export-dir',str(root/'exports'),
                    '--script-path',str(resources/'toolchain/skills/interpret-firmware-binary'),
                    '--prescript','SeedCortexM.java','--timeout-s','120'],capture_output=True,text=True,timeout=150)
                result=json.loads(completed.stdout)
                self.assertFalse(result['ok'])
                self.assertTrue(result.get('markers',{}).get('script_error'),result.get('reasons'))
                native_log=(root/'analysis/logs/analyzeHeadless.stdout').read_text()
                expected='Reset vector must select Thumb mode' if vector==0x08000008 else 'Reset vector must address mapped executable bytes'
                self.assertIn(expected,native_log)
