# SDK capability requests

Use `q.registry.search` and `q.requests.check` before requesting new work. Unsupported data raises `MissingCapabilityError` with `capability` and `required` fields. Historical L2, queue position, exact spread and liquidation events require dedicated verified sources.

```python
from quant import requests

request = requests.Request(
    name="rolling_hurst_exponent",
    module="quant.stats",
    purpose="Compare causal regime descriptors across development splits",
    required_inputs=("close",),
    parameters={"window": "scheduled bars", "estimator": "increments or rescaled_range"},
    causal=True,
    streaming_required=True,
    acceptance_criteria=(
        "Independent reference agreement",
        "Future perturbation, gaps and chunk-equivalence checks",
        "Benchmark on an audited development slice",
    ),
    output_units="dimensionless",
    missing_data="NaN for incomplete finite windows",
    warmup="Require a complete window",
)
request_id = requests.submit("bench-data/research.sqlite", request)
```

The SQLite queue deduplicates capability specifications and retains referring experiment IDs and reviewer history. Requests begin pending. A reviewer can accept or reject them. Closing an accepted request as implemented requires registered matching behavior plus a commit, tests and documentation.

Researchers compose experiments and request capabilities. A library engineer implements each reusable capability on a separate branch. A reviewer verifies definitions, causality, coverage and performance. Register the function and its documentation before research resumes. Changes to shared accounting, data eligibility, risk and evaluation rules follow the same reviewed workflow.
