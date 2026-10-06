"""Scripted contract fixtures; these toy files never qualify scored firmware."""
import json
import tempfile
import unittest
from pathlib import Path

from generative_driver.benchmark_support.asset_build import (
    build_argv, require_private_root, require_toolchain)

GCC = 'arm-none-eabi-gcc (Arm GNU Toolchain 14.2.Rel1 (Build arm-14.52)) 14.2.1 20241119'
OBJCOPY = 'GNU objcopy (Arm GNU Toolchain 14.2.Rel1 (Build arm-14.52)) 2.43.1.20241119'


class FamilyBuildTests(unittest.TestCase):
    def test_native_paths_with_spaces_are_individual_arguments(self):
        source = Path('C:/Evaluator Files/toy-case')
        elf = source / 'build/firmware.elf'
        compiler = Path('C:/Program Files/Arm/bin/arm-none-eabi-gcc.exe')
        argv = build_argv(compiler, source, elf, ['-Os', '-mcpu=cortex-m4', '-mthumb'])
        self.assertEqual(argv[0], str(compiler))
        self.assertIn(str(source / 'application.c'), argv)
        self.assertEqual(argv[-2:], ['-o', str(elf)])
        self.assertIn('-ffile-prefix-map=' + str(source.resolve()) + '=.', argv)
        self.assertFalse(any(x in ('sh', 'bash', 'cmd', '-c') for x in argv))

    def test_toolchain_requires_entire_recorded_versions(self):
        require_toolchain(GCC, OBJCOPY)
        for compiler, objcopy in [(GCC + ' altered', OBJCOPY), (GCC, OBJCOPY + ' altered'),
                                   ('arm-none-eabi-gcc 13.3.1', 'GNU objcopy 2.43.1')]:
            with self.subTest(compiler=compiler, objcopy=objcopy), self.assertRaises(ValueError):
                require_toolchain(compiler, objcopy)

    def test_authoring_in_forbidden_tree_or_symlink_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); checkout = root / 'repo'; checkout.mkdir()
            for path in (checkout, checkout / 'private'):
                with self.assertRaises(ValueError):
                    require_private_root(path, [checkout])
            self.assertEqual(require_private_root(root / 'private', [checkout]), (root / 'private').resolve())
            link = root / 'link'
            try:
                link.symlink_to(checkout, target_is_directory=True)
            except OSError:
                return
            with self.assertRaises(ValueError):
                require_private_root(link / 'private', [checkout])


class FamilyBuildPipelineTests(unittest.TestCase):
    def fixture(self, root):
        import hashlib
        from generative_driver.benchmark_support import asset_build
        author = root / 'toy author'
        author.mkdir()
        inputs = asset_build.inventory('parameter-store') - asset_build.BUILD_FILES
        for relative in inputs:
            path = author / relative; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{}\n' if path.suffix == '.json' else 'toy input\n')
        (author / 'AUTHORING.json').write_text(json.dumps({'schema': 'benchmark-authoring/1',
            'id': 'parameter-store-v1', 'family': 'parameter-store', 'execution': 'scripted-contract-fixture'}))
        (author / 'build-recipe.json').write_text(json.dumps({'schema': 'benchmark-family-build/1',
            'flags': ['-Os', '-mcpu=cortex-m4', '-mthumb', '-nostdlib'],
            'variants': {'firmware': ['-DWIRE_MULTIPLIER=1']}}))
        compiler = root / 'tools with spaces/arm-none-eabi-gcc'
        compiler.parent.mkdir(); compiler.touch()
        compiler.with_name('arm-none-eabi-objcopy').touch()
        script = root / 'toy_compiler.py'
        script.write_text('''import pathlib, sys
kind, *args = sys.argv[1:]
if args == ['--version']:
    print(%r if kind.endswith('gcc') else %r)
elif kind.endswith('gcc'):
    assert '-T' in args and any(x.endswith('application.c') for x in args)
    assert any(x.startswith('-ffile-prefix-map=') and x.endswith('=.') for x in args)
    pathlib.Path(args[args.index('-o')+1]).write_bytes(b'toy ELF' + ('drift' if '-DWIRE_MULTIPLIER=2' in args else 'original').encode())
else:
    assert args[:2] == ['-O', 'binary']
    pathlib.Path(args[-1]).write_bytes(pathlib.Path(args[-2]).read_bytes().replace(b'ELF', b'BIN'))
''' % (GCC, OBJCOPY))
        return author, compiler, script

    def runner(self, script, *, change_second=False, empty_version=False, fail=False):
        import subprocess
        import sys
        real = subprocess.run
        calls = [0]
        def execute(argv, **kwargs):
            if empty_version and argv[1:] == ['--version']:
                return subprocess.CompletedProcess(argv, 0, '', '')
            if fail and argv[1:] != ['--version']:
                raise subprocess.CalledProcessError(7, argv, output='', stderr='private toy diagnostic')
            result = real([sys.executable, str(script), Path(argv[0]).name, *argv[1:]], **kwargs)
            if '-O' in argv:
                calls[0] += 1
                if change_second and calls[0] > 1:
                    Path(argv[-1]).write_bytes(b'changed second build')
            return result
        return execute

    def test_build_twice_seal_and_unlock_exact_pending_inventory(self):
        import base64
        import hashlib
        from unittest.mock import patch
        from generative_driver.benchmark_support import asset_build, truth
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); author, compiler, script = self.fixture(root)
            with patch('generative_driver.benchmark_support.authoring.subprocess.run', side_effect=self.runner(script)):
                report = asset_build.build_family(author, compiler, root / 'private outputs')
            self.assertEqual(report['execution'], 'scripted-contract-fixture')
            self.assertEqual(report['reproducibility']['builds'], 2)
            self.assertTrue(report['reproducibility']['identical'])
            self.assertEqual((author / 'build/firmware.bin').read_bytes(), b'toy BINoriginal')
            password = root / 'password'; password.write_text('toy-only-test-password')
            release = root / 'release'
            result = asset_build.assemble_release(author, report, password, release)
            manifest = json.loads((release / 'cases/parameter-store-v1/case.json').read_text())
            self.assertEqual(manifest['calibration']['status'], 'pending')
            self.assertEqual(manifest['family'], 'parameter-store')
            self.assertEqual(manifest['scenarios'], ['original'])
            self.assertEqual(manifest['version'], '5')
            self.assertEqual(manifest['evaluator_version'], '5')
            self.assertEqual(set(manifest['images']), {'firmware.bin'})
            self.assertEqual(manifest['required_stages'], ['acquire','interpret','probe','ground','emit','reuse'])
            self.assertNotIn(str(author), json.dumps(manifest))
            unlocked = truth.unlock(release / manifest['truth']['path'], password.read_text(), manifest['truth']['sha256'])
            self.assertEqual(set(unlocked['inventory']), asset_build.inventory('parameter-store'))
            entry = unlocked['inventory']['build/firmware.bin']
            self.assertEqual(base64.b64decode(entry['base64']), b'toy BINoriginal')
            self.assertEqual(entry['sha256'], hashlib.sha256(b'toy BINoriginal').hexdigest())
            self.assertEqual(result['status'], 'pending')
            self.assertFalse((author / 'build.json').exists())
            self.assertEqual(len(list(release.rglob('*.*'))), 3)

    def test_nonreproducible_build_never_creates_canonical_images(self):
        from unittest.mock import patch
        from generative_driver.benchmark_support import asset_build
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); author, compiler, script = self.fixture(root)
            with patch('generative_driver.benchmark_support.authoring.subprocess.run', side_effect=self.runner(script, change_second=True)):
                with self.assertRaisesRegex(ValueError, 'reproduc'):
                    asset_build.build_family(author, compiler, root / 'outputs')
            self.assertFalse((author / 'build/firmware.bin').exists())

    def test_rejects_uninventoried_files_and_symlinks_before_build(self):
        from generative_driver.benchmark_support import asset_build
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); author, compiler, _ = self.fixture(root)
            extra = author / 'extra-secret'; extra.write_text('private')
            with self.assertRaises(ValueError):
                asset_build.build_family(author, compiler, root / 'outputs')
            extra.unlink()
            source = author / 'source/application.c'; source.unlink()
            outside = root / 'outside'; outside.write_text('private')
            try:
                source.symlink_to(outside)
            except OSError:
                return
            with self.assertRaises(ValueError):
                asset_build.build_family(author, compiler, root / 'outputs')

    def test_release_rejects_tampered_images_and_plaintext_output(self):
        from unittest.mock import patch
        from generative_driver.benchmark_support import asset_build
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); author, compiler, script = self.fixture(root)
            with patch('generative_driver.benchmark_support.authoring.subprocess.run', side_effect=self.runner(script)):
                report = asset_build.build_family(author, compiler, root / 'outputs')
            password = root / 'password'; password.write_text('toy-password')
            release = root / 'release'; release.mkdir(); (release / 'source.c').write_text('private')
            with self.assertRaises(ValueError):
                asset_build.assemble_release(author, report, password, release)
            (release / 'source.c').unlink()
            (author / 'build/firmware.bin').write_bytes(b'changed')
            with self.assertRaises(ValueError):
                asset_build.assemble_release(author, report, password, release)


class SharedRunnerBoundaryTests(unittest.TestCase):
    def test_prefix_maps_allow_only_inventoried_source_root_and_dot_destination(self):
        from unittest.mock import patch
        from test_benchmark_native import BuildTests
        from generative_driver.benchmark_support.authoring import build_case
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); author, manifest = BuildTests().authoring(root)
            source = author / 'source'; source.mkdir(); (source / 'toy.c').write_text('toy')
            import hashlib
            manifest['source_hashes']['source/toy.c'] = hashlib.sha256(b'toy').hexdigest()
            base = manifest['steps'][0]
            for flag in ['-ffile-prefix-map={authoring}/source=.', '-fdebug-prefix-map={authoring}=.']:
                # Python cannot accept GCC flags: the subprocess adapter keeps the actual
                # scripted output process, and checks the resolved argv before executing it.
                import subprocess
                real = subprocess.run
                def run(argv, **kwargs):
                    if '--version' not in argv:
                        self.assertIn(flag.split('=', 1)[0] + '=' + str((source if '/source' in flag else author).resolve()) + '=.', argv)
                        argv = argv[:1] + argv[2:]
                    return real(argv, **kwargs)
                manifest['steps'] = [[base[0], flag, *base[1:]]]
                (author / 'build.json').write_text(json.dumps(manifest))
                with patch('generative_driver.benchmark_support.authoring.subprocess.run', side_effect=run):
                    report = build_case(author, Path(sys.executable), root / ('out' + str(len(flag))))
                self.assertTrue(report['ok'])
            for flag in ['-ffile-prefix-map=/tmp=.', '-ffile-prefix-map={output}=.',
                         '-ffile-prefix-map={authoring}/../escape=.', '-ffile-prefix-map={authoring}/missing=.',
                         '-ffile-prefix-map={authoring}/source=/tmp', '@response', '-fdebug-prefix-map={authoring}/source=..']:
                manifest['steps'] = [[base[0], flag, *base[1:]]]
                (author / 'build.json').write_text(json.dumps(manifest))
                with self.subTest(flag=flag), self.assertRaises(ValueError):
                    build_case(author, Path(sys.executable), root / 'refused')
                self.assertFalse((root / 'refused').exists())

    def test_empty_version_and_process_failure_are_private_diagnostics(self):
        from unittest.mock import patch
        from generative_driver.benchmark_support import asset_build
        for kind in ('empty_version', 'fail'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); helper = FamilyBuildPipelineTests()
                author, compiler, script = helper.fixture(root)
                script.write_text("import sys\n" + ("sys.exit(0)\n" if kind == 'empty_version' else "sys.stderr.write('private toy diagnostic'); sys.exit(7)\n"))
                with patch('generative_driver.benchmark_support.authoring.subprocess.run', side_effect=helper.runner(script)):
                    with self.assertRaises(RuntimeError) as caught:
                        asset_build.build_family(author, compiler, root / 'outputs')
                self.assertNotIn('private toy diagnostic', str(caught.exception))
                failure = root / 'outputs/build-1/build-failure.json'
                self.assertTrue(failure.is_file())
                self.assertFalse((root / 'outputs/build-1/build-report.json').exists())


class BuildEdgeTests(unittest.TestCase):
    def test_shared_runner_rejects_empty_image_with_private_diagnostic(self):
        from test_benchmark_native import BuildTests
        from generative_driver.benchmark_support.authoring import build_case
        import sys
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); author, manifest = BuildTests().authoring(root)
            script = author / 'compile.py'
            script.write_text('import pathlib,sys\npathlib.Path(sys.argv[1]).touch()\n')
            manifest['source_hashes']['compile.py'] = hashlib.sha256(script.read_bytes()).hexdigest()
            (author / 'build.json').write_text(json.dumps(manifest))
            with self.assertRaises(RuntimeError):
                build_case(author, Path(sys.executable), root / 'output')
            self.assertTrue((root / 'output/build-failure.json').exists())
            self.assertFalse((root / 'output/build-report.json').exists())

    def test_output_containment_and_bad_recipe_are_rejected_before_build(self):
        from generative_driver.benchmark_support import asset_build
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); author, compiler, _ = FamilyBuildPipelineTests().fixture(root)
            for output in (author / 'out', author.parent):
                with self.assertRaises(ValueError):
                    asset_build.build_family(author, compiler, output)
            recipe = json.loads((author / 'build-recipe.json').read_text())
            recipe['flags'] = ['-I/private', '@escape']
            (author / 'build-recipe.json').write_text(json.dumps(recipe))
            with self.assertRaises(ValueError):
                asset_build.build_family(author, compiler, root / 'output')
            self.assertFalse((author / 'build/firmware.bin').exists())

    def test_rebuild_mismatch_preserves_previously_pinned_image(self):
        from unittest.mock import patch
        from generative_driver.benchmark_support import asset_build
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); helper = FamilyBuildPipelineTests(); author, compiler, script = helper.fixture(root)
            with patch('generative_driver.benchmark_support.authoring.subprocess.run', side_effect=helper.runner(script)):
                asset_build.build_family(author, compiler, root / 'build1')
            script.write_text(script.read_text().replace("b'toy ELF'", "b'changed ELF'"))
            with patch('generative_driver.benchmark_support.authoring.subprocess.run', side_effect=helper.runner(script)):
                with self.assertRaisesRegex(ValueError, 'pinned'):
                    asset_build.build_family(author, compiler, root / 'build2')
            self.assertEqual((author / 'build/firmware.bin').read_bytes(), b'toy BINoriginal')

    def test_scripted_manifest_does_not_claim_actual_native_build(self):
        from unittest.mock import patch
        from generative_driver.benchmark_support import asset_build
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); helper = FamilyBuildPipelineTests(); author, compiler, script = helper.fixture(root)
            with patch('generative_driver.benchmark_support.authoring.subprocess.run', side_effect=helper.runner(script)):
                report = asset_build.build_family(author, compiler, root / 'build1')
            password = root / 'password'; password.write_text('toy-password')
            asset_build.assemble_release(author, report, password, root / 'release')
            manifest = json.loads((root / 'release/cases/parameter-store-v1/case.json').read_text())
            self.assertNotIn('native', manifest['provenance']['build'])


class PlacementTests(unittest.TestCase):
    def test_installed_library_tree_is_not_an_authoring_location(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch('sysconfig.get_paths', return_value={'purelib': str(root / 'packages'), 'platlib': str(root / 'native-packages')}):
                for base in ('packages', 'native-packages'):
                    with self.subTest(base=base), self.assertRaises(ValueError):
                        require_private_root(root / base / 'unrelated-package', [])

class LegacyBuildTests(unittest.TestCase):
    def test_rebuild_uses_only_the_authenticated_original_source(self):
        import hashlib
        import subprocess
        from types import SimpleNamespace
        from unittest.mock import patch
        from generative_driver.benchmark_support.evaluator_cli import manage
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);compiler=root/'arm-none-eabi-gcc';compiler.touch()
            expected={'firmware.bin':hashlib.sha256(b'toy original').hexdigest()}
            manifest={'truth':{'path':'toy.enc'},'images':expected}
            truth={'build':{'flags':[]},'source_files':{'main.c':'toy source','link.ld':'toy link'}}
            def execute(argv, **kwargs):
                if argv[0].endswith('objcopy'):
                    Path(argv[-1]).write_bytes(b'toy original')
                else:
                    self.assertEqual((Path(kwargs['cwd'])/'main.c').read_text(),'toy source')
                    Path(argv[-1]).write_bytes(b'toy ELF')
                return subprocess.CompletedProcess(argv,0,'','')
            with patch('generative_driver.benchmark_support.registry.resolve_case',return_value=SimpleNamespace(manifest=manifest)),patch(
                'generative_driver.benchmark_support.emulator.truth_for_case',return_value=truth),patch(
                'generative_driver.benchmark_support.evaluator_cli.subprocess.run',side_effect=execute),patch(
                'generative_driver.benchmark_support.evaluator_cli.subprocess.check_output',return_value='toy compiler'):
                result=manage('rebuild','tq9',root/'password',root/'output',compiler=compiler)
            self.assertTrue(result['ok'])
            self.assertEqual(result['images'],expected)
            self.assertEqual({p.name for p in (root/'output').glob('*.bin')},{'firmware.bin'})

class DistributedInventoryTests(unittest.TestCase):
    def test_transaction_release_refuses_previous_emulator_version(self):
        from generative_driver.benchmark_support.registry import validate_public_workflow_identity
        for case in ('tq9-v2', 'sampled-sensor-v1', 'parameter-store-v1'):
            with self.subTest(case=case):
                with self.assertRaisesRegex(ValueError, 'version'):
                    validate_public_workflow_identity({'case_id':case, 'case_version':'4',
                        'evaluator_version':'4', 'scenario_id':'original', 'revision':0})

    def test_every_registered_firmware_pin_has_only_its_original_image_and_truth(self):
        from generative_driver.benchmark import case_root
        from generative_driver.benchmark_support.registry import pin_case
        root=case_root()
        cases={'tq9':'2','tq9-v2':'5','sampled-sensor-v1':'5','parameter-store-v1':'5','bme280':'3'}
        for case,version in cases.items():
            with self.subTest(case=case):
                pin=pin_case(case,'original',0)
                self.assertEqual(pin['manifest']['version'],version)
                self.assertEqual(pin['manifest']['evaluator_version'],version)
                expected={'case.json'} | ({'firmware.bin'} if case!='bme280' else set())
                self.assertEqual({p.name for p in (root/'cases'/case).iterdir()},expected)
        self.assertEqual({p.name for p in (root/'groundtruth').iterdir()},{case+'.enc' for case in cases})

    def test_retired_scenario_engine_is_not_distributed(self):
        import importlib.util
        self.assertIsNone(importlib.util.find_spec('generative_driver.benchmark_support.scenarios'))
