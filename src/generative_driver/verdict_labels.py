"""The words the tool reports a run's final verdict with.

A run ends with one final verdict: the tool's own, or one posted to the run. The configurator gives a completed run
its reason from that verdict, and the dashboard shows it by the same word. The tool keeps the wording here, in its
own package, so that each name has one place.
"""

__all__ = ['equivalence_label', 'verdict_reason']


def equivalence_label(equivalence):
    """The word for the final verdict record `equivalence`: `equivalent`, `not equivalent`, `unknown` for a record
    whose status says so, or `unscored` for any other record that holds no verdict, and for none at all."""
    equivalence = equivalence or {}
    if equivalence.get('status') == 'unknown':
        return 'unknown'
    return {True: 'equivalent', False: 'not equivalent'}.get(equivalence.get('equivalent'), 'unscored')


def verdict_reason(equivalence):
    """The reason a completed run shows for its final verdict record `equivalence`: `Final verdict: ` and the word
    for it, followed, unless it is equivalent, by the reasons of its first three differences and the count of the
    rest. A difference counts when it is a dict whose `reason` is a string."""
    label = equivalence_label(equivalence)
    reasons = [d['reason'] for d in equivalence.get('diff') or []
               if isinstance(d, dict) and isinstance(d.get('reason'), str)]
    detail = '; '.join(reasons[:3]) + ('; ' + str(len(reasons) - 3) + ' more' if len(reasons) > 3 else '')
    return 'Final verdict: ' + label + (': ' + detail if label != 'equivalent' and reasons else '')
