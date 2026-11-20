"""The documentation of a package, rendered from its drivers and its evidence.

A package holds no text a worker wrote: `README.md` is what this module gives for the package's interface models and
probe records, so the emit stage can compare the file with it (docs/adr/0010-a-package-is-data-in-one-format.md). The
words on an operation are the ones the tools carry (`interface_runtime.catalog`).
"""
import json

from interface_runtime.catalog import describe
from interface_runtime.contracts import contract_view
from interface_runtime.evidence import COVERAGE_SCOPE, probe_coverage

# The binding the operator supplies, by channel type: its form on the command line and what its fields are.
BINDINGS = {
    'uart': ('{"port": "<serial device path>"}', 'port is the host serial device the operator chooses.'),
    'tcp': ('{"host": "<host name or address>", "port": <int>}', 'port is a JSON integer.'),
    'i2c': ('{"url": "ftdi://<adapter>/<interface>"}', 'url selects the FTDI adapter and must start with ftdi://.'),
    'spi': ('{"url": "ftdi://<adapter>/<interface>"}', 'url selects the FTDI adapter and must start with ftdi://.'),
    'usb': ('{"vendor_id": <int>, "product_id": <int>, "serial_number": "<str>"}',
            'vendor_id and product_id are JSON integers (decimal, never hex strings) that the operator supplies; '
            'serial_number selects one unit and may be left out only when exactly one matching device is attached. '
            'Two optional booleans, both false by default, widen what a session may do: allow_set_configuration lets '
            'it select the default configuration of an unconfigured device, and allow_detach_kernel_driver lets it '
            'detach a kernel driver from the interface the model names and reattach it on close.'),
}
NOT_SHOWN = ('Replay shows that the driver is consistent with the recorded exchanges, not that it is physically '
             'correct. No accuracy and no full coverage of the device is implied. A grant permits what the driver '
             'declares; it is no proof that the bytes an operation sends are safe.')


def _cell(lines):
    """Lines of text as one cell of a Markdown table."""
    return '<br>'.join(line.replace('|', '\\|').replace('\r', '').replace('\n', '<br>') for line in lines) or 'none'


def _values(definitions, output=False):
    return _cell(['`' + name + '`: ' + line if index == 0 else line for name, definition in definitions.items()
                  for index, line in enumerate(describe(definition, output))])


def _operations(model):
    rows = ['| Operation | Effect | Meaning | Parameters | Outputs | Warnings and preconditions |',
            '| --- | --- | --- | --- | --- | --- |']
    for name, operation in contract_view(model)['operations'].items():
        meaning, *caveats = describe(operation)
        rows.append(f"| {name} | {operation['effect']} | {_cell([meaning])} | {_values(operation['parameters'])} | "
                    f"{_values(operation['outputs'], output=True)} | {_cell(caveats)} |")
    return rows


def _serve(model):
    kind = model['channel']['type']
    form, note = BINDINGS[kind]
    needing = {}
    for name, operation in model['operations'].items():
        needing.setdefault(operation['effect'], []).append(name)
    flags = [f'- `--allow-effect {effect}` for {", ".join(needing[effect])}'
             for effect in ('write', 'actuate') if effect in needing]
    if model['channel'].get('resets_on_open'):
        flags.append('- `--allow-effect reset` for every operation, because this channel resets the device when it '
                     'opens')
    install = ('The connection needs no optional install.' if kind == 'tcp' else
               'The connection needs the optional install `generative-driver[hardware]`.')
    return ['```sh', f"generative-driver serve PACKAGE --binding '{form}'", '```', '',
            f'PACKAGE is the folder of this package. The channel is {kind}: {note} The binding is the operator\'s '
            'choice; a driver cannot select a host path, a device or a network destination.', '',
            *(['A read needs no flag. A call of another operation is refused unless the command was started with the '
               'flag its effect needs, once for each effect:', '', *flags] if flags
              else ['Every operation is a read, and a read needs no flag.']),
            '', install]


def _tested(model, probe):
    rows = ['| Operation | Status | Samples attempted / passed / failed | Evidence | Parameters |',
            '| --- | --- | --- | --- | --- |']
    for name, entry in probe_coverage(model, probe).items():
        counts = entry['samples']
        parameters = ('' if entry['parameters'] is None else
                      '`' + json.dumps(entry['parameters'], sort_keys=True, ensure_ascii=True).replace('|', '\\u007c')
                      + '`')
        rows.append(f"| {name} | {entry['status']} | {counts['attempted']} / {counts['passed']} / {counts['failed']} | "
                    f"{entry['evidence_kind'] or 'none'} | {parameters} |")
    return rows


def render_readme(drivers, evidence):
    """The text of `README.md` for `drivers`, which maps the name of the package's one driver to its interface model,
    and `evidence`, which maps the same name to the probe record that driver wrote: the device's name, its operations,
    how to serve the package, what was tested and what the evidence does not show."""
    [(driver, model)] = drivers.items()
    probe = evidence[driver]
    return '\n'.join([
        '# ' + (model['device']['name'] or driver), '',
        '## Operations', '', *_operations(model), '',
        '## Serve', '', *_serve(model), '',
        '## Tested', '',
        f"The evidence is one probe record of the operation {probe['operation']}, "
        f"of the kind {probe['evidence_kind']}.",
        '', *_tested(model, probe), '', COVERAGE_SCOPE, '',
        '## What the evidence does not show', '', NOT_SHOWN, ''])
