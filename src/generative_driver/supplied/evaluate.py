"""The wiring of an interpreted model to the TCP binding of a supplied device. Nothing here grades. It is part of the
tool's stage package."""
import hashlib
import json
from pathlib import Path


def adapt_model(original, destination):
    """The record of the explicit UART-to-TCP wiring of the model at `original`, written into `destination` beside
    the wired model. The inferred serial settings are kept in the record, apart from the model."""
    original, destination = Path(original), Path(destination)
    model = json.loads(original.read_text(encoding='utf-8'))
    channel = model['channel']
    if channel.get('type') not in ('uart', 'tcp'):
        raise ValueError('This emulator connects the recovered host command interface over a UART socket')
    destination.mkdir(parents=True, exist_ok=True)
    model['channel'] = {'type': 'tcp'}
    # Explicit UTF-8 and LF: the package carries these bytes, and a host's newline translation must not change them.
    (destination / 'model.json').write_text(json.dumps(model, indent=2) + '\n', encoding='utf-8', newline='\n')
    record = {'kind': 'emulator UART-to-TCP wiring', 'original_model_sha256': hashlib.sha256(original.read_bytes()).hexdigest(),
              'original_channel': channel, 'adapted_model_sha256': hashlib.sha256((destination/'model.json').read_bytes()).hexdigest(),
              'limitation': 'TCP does not measure UART baud, parity or pin configuration'}
    (destination / 'adaptation.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8', newline='\n')
    return record
