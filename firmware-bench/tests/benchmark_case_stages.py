"""The tool's stage functions, called for a registered benchmark case.

The stage functions of `generative_driver.supplied.stages` take a firmware and a mission as data. A benchmark test
that runs a stage of a case asks the benchmark for the case's two, as the service does, and these helpers do that for
it (Feature 05 of the untangle Run, docs/features/untangle/05-supplied-device-stages.md). This file is a benchmark
item: it goes where the benchmark goes.
"""
from firmware_bench import benchmark
from generative_driver.supplied import stages


def prepare_stage(case_id, stage, run_dir, workspace, accepted=None, options=None):
    """What `stages.prepare_stage` returns for `stage` on the firmware and the mission of the case `case_id`."""
    return stages.prepare_stage(stage, run_dir, workspace, accepted, options,
                                **benchmark.supplied_inputs(case_id, run_dir, options))


def observe_stage(case_id, stage, run_dir, workspace, report, accepted=None, options=None):
    """What `stages.observe_stage` returns for `stage` on the firmware and the mission of the case `case_id`."""
    return stages.observe_stage(stage, run_dir, workspace, report, accepted, options,
                                **benchmark.supplied_inputs(case_id, run_dir, options))
