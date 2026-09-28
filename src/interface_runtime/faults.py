"""Fault ownership shared by execution, probe and package callers.

New failures declare ownership where it is known. The resolver accepts older evidence too; ambiguous
historical binding failures and unknown codes remain host diagnostics, never instructions to repair a model.
"""
from enum import Enum


class Fault(str, Enum):
    OPERATOR = 'operator'
    HOST = 'host'
    MODEL = 'model'


# Kept for callers of the former two-way classifier. New callers use classify_failure(result).
LEGACY_TRANSPORT_CODES = frozenset({'binding', 'open', 'dependency', 'io', 'timeout', 'disconnect', 'closed'})
MODEL_CODES = frozenset({'channel', 'invalid', 'unsupported', 'framing', 'length', 'limit',
                         'validation_error', 'execution_error', 'identity_unverified'})


def classify_failure(result):
    """Return operator, host, model, or None for success, including historical runtime results.

    A timeout belongs to the model only after identity succeeded in this execution. Explicit ownership
    resolves codes such as binding or validation_error whose original spelling alone was ambiguous.
    """
    if result.get('ok') is True:
        return None
    error = result.get('error') if isinstance(result.get('error'), dict) else {}
    declared = error.get('fault')
    if declared in tuple(f.value for f in Fault):
        return declared
    code = error.get('code')
    if code == 'timeout':
        return Fault.MODEL.value if result.get('identity_verified') is True else Fault.HOST.value
    if code == 'validation_error' and error.get('message') in (
            'operation requires explicit effect grant', 'invalid effect grants', 'binding must be object',
            'unknown operation', 'argument violates parameter type or bounds', 'arguments must match parameter names exactly'):
        return Fault.OPERATOR.value
    return Fault.MODEL.value if code in MODEL_CODES else Fault.HOST.value
