"""Sampled sensor checks from independent latched source and acquisition counter."""
from .family_support import integer, private_phase, signed16


def contract(pin, truth, phase):
    return private_phase(pin, truth, phase)["contract"]


def build_plan(pin, truth, phase):
    return private_phase(pin, truth, phase)["actions"]


def observations(raw, canonical_task_result, inputs):
    values = raw["values"]
    reference = signed16(integer(values, "latched_q4")) / 16
    sequence = integer(values, "acquisition_counter")
    outputs = canonical_task_result.get("outputs", {})
    return {"temperature": outputs.get("temperature"),
            "reference_temperature": reference,
            "sequence": outputs.get("sequence"), "monitor_sequence": sequence,
            "operation_ok": canonical_task_result.get("ok") is True}


observations.monitor_units = {"reference_temperature": "degC", "monitor_sequence": "count"}
