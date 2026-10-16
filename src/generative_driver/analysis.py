"""The same generic firmware-analysis inputs for device runs and benchmark workers."""
import json
from pathlib import Path


def firmware_analysis_inputs(directory, options):
    from .toolkit import resources_root
    resources = resources_root()
    config = Path(directory) / 'ANALYSIS_TOOLS.json'
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({
        **{key: str(Path(options[key]).expanduser().resolve())
           for key in ('ghidra_home', 'java_home') if options.get(key)},
        'instructions': 'Run the supplied ghidra_run.py --help for the native analyzer interface. '
            'Use the configured ghidra_home and java_home when provided. If native tools are unavailable, '
            'report that limitation. Determine architecture and mapping from the image evidence.',
        'optional_cortex_m_prescript': {
            'when': 'Use only when image evidence establishes a Cortex-M vector table at the start of the '
                'imported mapping and a Thumb reset entry inside executable bytes. This generic helper does '
                'not identify the architecture or infer a load map.',
            'usage': 'After independently establishing loader, processor and mapping, add --prescript '
                'SeedCortexM.java --script-path <absolute-workspace> to the supplied ghidra_run.py command. '
                'Keep the reasoning and native logs in this workspace.'}}, indent=1), encoding='utf-8')
    return {'ghidra_run.py': str(resources / 'tools/workspace/ghidra_run.py'),
            'ExportDecomp.java': str(resources / 'toolchain/skills/interpret-firmware-binary/ExportDecomp.java'),
            'SeedCortexM.java': str(resources / 'toolchain/skills/interpret-firmware-binary/SeedCortexM.java'),
            'ANALYSIS_TOOLS.json': str(config)}
