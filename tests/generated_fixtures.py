"""A hand-written generated-device set for tests, sealed under a synthetic password. Never generator output."""
from generative_driver.benchmark_support.generated_sets import seal_device, write_manifest

PASSWORD = 'synthetic fixture credential'
FIXTURE_IMAGE = b'fixture firmware image'
# A placeholder until the generated family fixes the content of an answer key.
FIXTURE_ANSWER_KEY = {'schema': 'generated-answer-key/1'}


def fixture_mission(device_id):
    """A small public mission, different for each device so that a test can tell the devices apart."""
    return {'device': device_id, 'objective': 'Report the mode of the fixture device and set it on request.'}


def write_fixture_set(root):
    """Seal devices d001 and d002 (level 1, text lines) under `root`, write the manifest of set fixture-v1 beside them
    and return the SHA-256 of that manifest, which a test registers."""
    devices = [seal_device(root, {'id': device_id, 'level': 1, 'framing': 'text-lines'}, image=FIXTURE_IMAGE,
                           mission=fixture_mission(device_id), answer_key=FIXTURE_ANSWER_KEY, password=PASSWORD)
               for device_id in ('d001', 'd002')]
    return write_manifest(root, 'fixture-v1', devices)
