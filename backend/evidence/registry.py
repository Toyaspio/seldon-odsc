"""Canonical citation identities for a single research task."""


def resolve_source_references(references, source_ids, metric_sources):
    matched = {}
    missing = []
    for reference in references:
        if reference in source_ids:
            matched[reference] = None
            continue
        aliases = metric_sources.get(reference, ())
        if aliases and all(source_id in source_ids for source_id in aliases):
            matched.update(dict.fromkeys(aliases))
        else:
            missing.append(reference)
    return list(matched), missing


class EvidenceRegistry:
    def __init__(self, sources, metrics=()):
        self._sources = {source.source_id: source for source in sources}
        self._metric_sources = {}
        for metric in metrics:
            if metric.actual_or_guidance != 'actual' or not metric.evidence_ids:
                continue
            if all(source_id in self._sources for source_id in metric.evidence_ids):
                self._metric_sources[metric.metric_id] = tuple(metric.evidence_ids)

    def sources(self):
        return list(self._sources.values())

    def get(self, source_id):
        return self._sources.get(source_id)

    def resolve(self, references, source_ids, metric_ids=()):
        """Resolve citations to the source records available for the analysis."""
        return resolve_source_references(
            references, source_ids, {key: self._metric_sources.get(key, ()) for key in metric_ids})
