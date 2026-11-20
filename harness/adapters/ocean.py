"""Ocean.io adapter: one company search seeded with the seed's domain.

`firmographic_filters` (build_request) handles the `seed_firmographic` setup.
It maps the seed's public hints (employee range + locations) to
Ocean's `companiesFilters` shape (`companySizes` bands +
`primaryLocations.includeCountries`) and stashes them in template vars
(`company_sizes` / `primary_locations`) that the request body references and
prunes when null. It raises `SkipConfig` when a `use_seed_firmo` setup lands on
a seed with no hints.

Ocean's static setups (seed_only / mid_market / us_uk_de_funded) layer their
filters via the request's `merge` directive (`config.filters → companiesFilters`),
so this hook is a no-op for them.
"""
from __future__ import annotations

from typing import Any

from ..models import RunResult, Seed, SkipConfig
from ..spec_runner import run_from_spec
from ..tool_specs import build_spec

# Ocean's discrete employee-count bands. A seed's [min,max] employee hint maps
# to every overlapping band.
_OCEAN_BANDS: list[tuple[int, int, str]] = [
    (0, 1, "0-1"),
    (2, 10, "2-10"),
    (11, 50, "11-50"),
    (51, 200, "51-200"),
    (201, 500, "201-500"),
    (501, 1000, "501-1000"),
    (1001, 5000, "1001-5000"),
    (5001, 10000, "5001-10000"),
    (10001, 50000, "10001-50000"),
    (50001, 100000, "50001-100000"),
    (100001, 500000, "100001-500000"),
    (500001, 10**9, "500000+"),
]


def _size_bands(min_emp: int | None, max_emp: int | None) -> list[str]:
    lo = min_emp if min_emp is not None else 0
    hi = max_emp if max_emp is not None else 10**9
    return [label for blo, bhi, label in _OCEAN_BANDS if blo <= hi and bhi >= lo]


def firmographic_filters(ctx: dict[str, Any]) -> None:
    """build_request: for `use_seed_firmo` configs, derive Ocean filter vars from
    the seed's firmographic hints (skip cleanly when the seed has none)."""
    config = ctx["config"]
    if not config.get("use_seed_firmo"):
        return  # static configs layer filters via the spec `merge` directive
    seed: Seed = ctx["seed"]
    firmo = seed.firmographics
    if not firmo:
        raise SkipConfig("ocean: firmographic config skipped (no hints for seed)")
    vars = ctx["vars"]
    bands = _size_bands(firmo.get("min_employees"), firmo.get("max_employees"))
    vars["company_sizes"] = bands or None
    locs = firmo.get("locations") or []
    vars["primary_locations"] = (
        {"includeCountries": [str(c).lower() for c in locs]} if locs else None
    )


SPEC = build_spec("ocean", {
    "base_url": "https://api.ocean.io",
    "requires_seed_domain": True,
    "auth": [{"header": "X-Api-Token", "env": "OCEAN_API_KEY"}],
    "request": {
        "method": "POST",
        "path": "/v3/search/companies",
        "body": {
            "size": "$k",
            "companiesFilters": {
                "lookalikeDomains": ["$seed_domain"],
                "excludeDomains": ["$seed_domain"],   # drop the seed itself
                # Set by firmographic_filters only for the seed_firmographic setup;
                # null for static setups (pruned), which layer filters via merge below.
                "companySizes": "$company_sizes",
                "primaryLocations": "$primary_locations",
            },
        },
        "merge": [{"from_config": "filters", "into_body": "companiesFilters"}],
    },
    "response": {
        "candidates_path": ["companies", "results"],
        # Ocean wraps each match as {company:{...}, relevance:...}; fall back to a
        # flat shape for older endpoints.
        "item": {
            "name": ["company.name", "company.companyName", "name", "companyName"],
            "domain": ["company.domain", "company.website", "company.rootUrl", "domain", "website", "rootUrl"],
            "description": ["company.description", "company.about", "description", "about"],
            "rank": None,
            "extra": {
                "country": ["company.primaryCountry", "company.countryCode"],
                "countries": "company.countries",
                "industries": ["company.industries", "company.industryCategories"],
                "linkedin_industry": "company.linkedinIndustry",
                "company_size": "company.companySize",
                "employees_ocean": "company.employeeCountOcean",
                "employees_linkedin": "company.employeeCountLinkedin",
                "funding_round": "company.fundingRound",
                "technologies": "company.technologies",
                "relevance_score": "relevance",
            },
        },
    },
    "hooks": {"build_request": firmographic_filters},
})
VENDOR_SLUG = SPEC.slug
VENDOR_NAME = SPEC.name
CONFIGS: list[dict[str, Any]] = SPEC.configs


def run(seed: Seed, k: int, config: dict[str, Any]) -> RunResult:
    return run_from_spec(SPEC, seed, k, config)
