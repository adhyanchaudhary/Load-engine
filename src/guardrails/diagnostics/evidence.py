"""Evidence references are application-generated, never source-code guesses."""
from guardrails.diagnostics.models import Evidence, Window


class EvidenceBuilder:
    def __init__(self, test_id, start, end):
        self.test_id = test_id
        aware = start is not None and start.tzinfo is not None and end is not None and end.tzinfo is not None
        self.window = Window(start=start if aware else None, end=end if aware else None,
                             clock_origin='UTC' if aware else 'unknown')
        self.items = []

    def add(self, source, reference, value, explanation, unit=None, limitations=None,
            interpretation='OBSERVATION', window=None):
        eid = f'e{len(self.items)+1}'
        self.items.append(Evidence(evidence_id=eid, source_type=source,
            source_reference=f'test:{self.test_id}:{reference}', observed_value=value, unit=unit,
            observation_window=window or self.window, explanation=explanation,
            limitations=limitations or [], interpretation=interpretation))
        return eid
