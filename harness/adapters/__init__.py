"""One adapter per tool. Each module exposes:

    VENDOR_SLUG: str
    VENDOR_NAME: str
    CONFIGS: list[dict]                       # the setups from tools/<tool>.yaml
    def run(seed, k, config) -> RunResult     # one API call → top-K candidates

The runner sweeps every setup in `CONFIGS` and keeps the one with the best
Precision@K for the seed.

Lusha is not registered: its lookalike endpoint needs 5-100 seeds per request,
and the benchmark scores one seed at a time.

ZoomInfo runs through the `gtm` CLI rather than an HTTP API, so it needs the
binary on PATH instead of an API key; without it the cell fails with
"gtm CLI is not installed".
"""
from . import (
    companyenrich,
    cufinder,
    discolike,
    exa,
    extruct,
    ocean,
    parallel,
    predictleads,
    seltz,
    zoominfo,
)

REGISTRY = {
    ocean.VENDOR_SLUG: ocean,
    exa.VENDOR_SLUG: exa,
    extruct.VENDOR_SLUG: extruct,
    cufinder.VENDOR_SLUG: cufinder,
    discolike.VENDOR_SLUG: discolike,
    parallel.VENDOR_SLUG: parallel,
    predictleads.VENDOR_SLUG: predictleads,
    seltz.VENDOR_SLUG: seltz,
    zoominfo.VENDOR_SLUG: zoominfo,
    companyenrich.VENDOR_SLUG: companyenrich,
}
