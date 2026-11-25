"""Discover public SDK exports and generate a machine-readable catalog."""

from functools import lru_cache
from importlib import import_module
import inspect
import json
from pathlib import Path
import re

import numpy as np

from . import __all__ as _modules

_CAUSAL = {
    "math",
    "ts",
    "indicators",
    "cs",
    "volatility",
    "microstructure",
    "signal",
    "portfolio",
}
_STATELESS_NAMES = {
    "math": "absolute sign sqrt power log exp log1p expm1 minimum maximum clip safe_divide where finite missing",
    "cs": "rank percentile zscore robust_zscore demean winsorize quantile_groups top_k bottom_k gross_normalize "
    "long_short_normalize market_neutralize beta_neutralize residualize group_neutralize liquidity_weighted_normalize",
    "microstructure": "volume_imbalance quote_volume_imbalance trade_count_imbalance vwap_deviation trade_intensity "
    "average_aggregate_trade_size average_constituent_trade_size large_aggregate_trade_proxy return_per_volume "
    "mark_to_trade_deviation",
    "signal": "sign clip normalize rank threshold deadband conditional_activate regime_filter",
    "portfolio": "gross_normalize long_short_budget dollar_neutral beta_neutral signal_weights equal_weight rank_weight inverse_volatility "
    "volatility_target cap_exposures concentration_limit turnover_rebalance signed_notionals position_size",
    "volatility": "classify_regime",
}
_FINITE_NAMES = set(
    """lag diff pct_change returns log_returns rolling_sum rolling_mean rolling_median rolling_min rolling_max
rolling_argmin rolling_argmax rolling_variance rolling_std rolling_covariance rolling_correlation rolling_quantile
rolling_rank rolling_percentile rolling_skewness rolling_kurtosis rolling_zscore rolling_mad rolling_count
rolling_regression rolling_beta rolling_residual sma wma linear_decay breakout""".split()
)
# Tuple-valued rolling_regression has a finite bound but the expression runner emits only scalar fields.
_FINITE_NAMES.remove("rolling_regression")
_RECURSIVE = {"ema", "ewma", "exponential_decay", "wilder"}
_TYPES = {
    "ts": ("EMAState", "EMAState.update", "WilderState", "WilderState.update"),
    "data": ("Selection", "Block"),
    "evaluate": ("Labels", "ScreenCandidate"),
    "simulator": (
        "Targets",
        "Result",
        "Result.table",
        "Result.iter_batches",
        "Candidate",
        "BatchResult",
    ),
    "features": ("Cache", "Cache.get", "Cache.put", "Feature"),
    "stats": ("Regression",),
    "requests": ("Request",),
    "validation": ("Split",),
}
_EXPENSIVE = {
    "rolling_mad",
    "rolling_rank",
    "rolling_percentile",
    "rolling_skewness",
    "rolling_kurtosis",
    "rolling_argmin",
    "rolling_argmax",
    "wma",
    "linear_decay",
    "cci",
    "aroon",
}
_METADATA = {
    "data": (
        "USD-M market bars and coverage",
        "Half-open regular UTC [time, symbol] blocks.",
        "Field units from the data contract.",
        "Unverified values remain missing with explicit status.",
        "Complete resampling buckets only.",
    ),
    "math": (
        "Real numeric observations",
        "Elementwise broadcasting or reductions over the supplied axis.",
        "Input units for magnitude reductions; dimensionless for proportions, correlations and transforms.",
        "Transforms use NaN for invalid results; finite-observation reductions omit nonfinite values.",
        "No temporal warm-up.",
    ),
    "ts": (
        "Regular sampled values",
        "[time] or [time, symbol]; axis 0 is time.",
        "Input units except returns, counts, ranks, z-scores and normalized relationships.",
        "NaNs are never converted to observations; rolling uses finite count and recursive operators specify gap policy.",
        "Trailing complete window unless min_periods specified; EMA starts at first finite input by default.",
    ),
    "indicators": (
        "Regular OHLC and projected flow as specified",
        "[time] or [time, symbol].",
        "Prices and base turnover unless definition gives a percentage or ratio.",
        "Unknown values propagate; recursive smoothers reset by default.",
        "Full windows and cascaded warm-up as defined.",
    ),
    "stats": (
        "Research samples or aligned paired sample arrays",
        "Sample axis 0; regression predictors are columns.",
        "Definition specific; p-values and correlations are dimensionless.",
        "HAC, resampling and stationarity methods require complete samples.",
        "Check degrees of freedom and estimator assumptions.",
    ),
    "cs": (
        "Aligned score and active-mask matrices",
        "[time, symbol], with per-row operations.",
        "Score units for location/dispersion; ranks and exposure weights are dimensionless.",
        "Finite available members only; insufficient rows remain missing.",
        "No temporal warm-up; minimum members applies.",
    ),
    "volatility": (
        "Regular returns or OHLC fields as specified",
        "[time] or [time, symbol].",
        "Return fraction per bar or per 365-day year when annualized.",
        "Unknown values are missing, with finite sample windows as configured.",
        "Full trailing window by default.",
    ),
    "microstructure": (
        "Observed projected aggregate-trade fields or supplied marks/funding as specified",
        "Aligned [time] or [time, symbol].",
        "Base quantity, USDT, seconds or dimensionless according to formula.",
        "Unknown data and undefined ratios remain NaN.",
        "Trailing-window functions require full windows by default.",
    ),
    "signal": (
        "Causal score and event arrays",
        "[time] or [time, symbol]; cross-sectional aliases require 2D.",
        "Score units, signed direction or boolean events.",
        "Missing inputs follow the specific transition rule.",
        "Crossings require a prior observation; state rules specify entry or confirmation.",
    ),
    "portfolio": (
        "Causal scores, equity or supplied covariance, depending on function",
        "[time, symbol] exposures; covariance is [symbol, symbol].",
        "Exposure weights are dimensionless; signed target notionals are USDT.",
        "Unavailable assets remain missing; solvers require finite feasible inputs.",
        "Caller supplies a historically observable risk estimate.",
    ),
    "risk": (
        "Portfolio exposures, marked equity, returns or scenario shocks",
        "Per symbol on last axis; risk reports use supplied sample.",
        "USDT exposure, dimensionless leverage and return risk.",
        "Unknown liabilities propagate in portfolio totals.",
        "No warm-up except trailing operators.",
    ),
    "evaluate": (
        "Forward Labels, causal scores and optional evaluation grouping",
        "[time, symbol] with exact axes when passing a Block.",
        "Returns and associations are dimensionless.",
        "Drops paired missing samples; labels crossing unknown coverage are invalid.",
        "Forward horizons require a complete path and observable endpoint.",
    ),
}
_REQUIRED = {
    "data.iter_blocks": ("timestamp_ms", "is_empty", "coverage"),
    "evaluate.forward_returns": ("close", "coverage", "available_at_ms"),
    "indicators.atr": ("high", "low", "close"),
    "indicators.rolling_vwap": ("quote_volume", "volume"),
    "microstructure.rolling_vwap": ("quote_volume", "volume"),
    "simulator.run": (
        "close",
        "volume",
        "quote_volume",
        "is_empty",
        "funding",
        "mark",
        "coverage",
    ),
}
_EXAMPLES = {
    "ts.ema": "q.ts.ema([1., 2., 3.], span=3)",
    "math.safe_divide": "q.math.safe_divide([4., 2.], [2., 0.])",
    "data.open": "q.data.open()",
    "microstructure.volume_imbalance": "q.microstructure.volume_imbalance([2.], [1.])",
    "cs.rank": "q.cs.rank([[3., 2., 2.]])",
    "registry.describe": "q.registry.describe('quant.ts.ema')",
    "ts.rolling_mean": "q.ts.rolling_mean([1., 2., 3.], 2)",
}
_RESEARCH_TAINT = {
    "screen",
    "screen_decay",
    "screen_walk_forward",
    "forward_returns",
    "iter_forward_returns",
    "research_values",
    "information_coefficient",
    "per_symbol_ic",
    "cross_sectional_ic",
    "rolling_ic",
    "alpha_decay",
    "quantile_returns",
    "quantile_spread",
    "conditional_returns",
    "event_study",
    "long_short_asymmetry",
    "regime_performance",
    "monthly_stability",
    "incremental_ic",
}


def _signature(f):
    try:
        return inspect.signature(f)
    except ValueError:
        wrapped = getattr(f, "__wrapped__", None)
        if isinstance(wrapped, np.ufunc):
            return inspect.Signature(
                [
                    inspect.Parameter(name, inspect.Parameter.POSITIONAL_OR_KEYWORD)
                    for name in ("x", "y")[: wrapped.nin]
                ]
            )
        return inspect.signature(f, follow_wrapped=False)


class _Default:
    def __init__(self, value):
        self.value = value

    def __repr__(self):
        return _canonical(self.value)


def _describe(name, function):
    _, category, short = name.split(".", 2)
    sig = _signature(function)
    display = sig.replace(
        parameters=[
            p.replace(default=_Default(p.default))
            if p.default is not inspect.Parameter.empty and callable(p.default)
            else p
            for p in sig.parameters.values()
        ]
    )
    doc = (inspect.getdoc(function) or "").splitlines()[0]
    inputs, shape, units, missing, warm = _METADATA.get(
        category,
        (
            "See argument signature.",
            "Structured configuration, samples or artifacts as specified.",
            "Definition specific.",
            "Invalid arguments raise an explicit exception.",
            "No implicit temporal warm-up.",
        ),
    )
    causal = (
        "causal"
        if category in _CAUSAL
        else "research-only"
        if short in _RESEARCH_TAINT and category == "evaluate"
        else (
            "sample-level"
            if category in {"evaluate", "stats", "performance", "risk", "optimize"}
            else "infrastructure"
        )
    )
    streaming = False
    if short in _STATELESS_NAMES.get(category, "").split():
        streaming = "stateless"
    if category == "ts":
        if short in _FINITE_NAMES:
            streaming = "finite-lookback"
        elif short in _RECURSIVE:
            streaming = "recursive-state"
        elif short in {"EMAState.update", "WilderState.update"}:
            streaming = "state-method"
    if category in _CAUSAL and function is resolve("quant.ts.ema"):
        streaming = "recursive-state"
    if (category, short) in {
        ("indicators", "sma"),
        ("indicators", "wma"),
        ("indicators", "vwma"),
        ("indicators", "rolling_vwap"),
        ("indicators", "realized_volatility"),
        ("volatility", "realized_volatility"),
        ("microstructure", "rolling_vwap"),
        ("microstructure", "volume_zscore"),
    }:
        streaming = "finite-lookback"
    if (category, short) in {
        ("data", "iter_blocks"),
        ("data", "resample"),
        ("data", "load_funding"),
        ("data", "load_marks"),
        ("features", "evaluate"),
        ("evaluate", "iter_forward_returns"),
        ("simulator", "Result.iter_batches"),
    }:
        streaming = "block-iterator"
    if (category, short) in {
        ("simulator", "run_batch"),
        ("evaluate", "screen"),
        ("evaluate", "screen_decay"),
        ("evaluate", "screen_walk_forward"),
    }:
        streaming = "block-processing"
    definition = doc
    if function is resolve("quant.ts.ema"):
        definition = "EMA[t] = alpha*x[t] + (1-alpha)*EMA[t-1], alpha=2/(span+1); first finite value seeds state."
    required = _REQUIRED.get(f"{category}.{short}")
    if required is None:
        field_names = {
            "open",
            "high",
            "low",
            "close",
            "volume",
            "quote_volume",
            "buy_volume",
            "sell_volume",
            "buy_quote_volume",
            "sell_quote_volume",
            "agg_trade_count",
            "trade_count",
            "buy_trade_count",
            "sell_trade_count",
            "max_trade_qty",
            "vwap",
        }
        required = tuple(p for p in sig.parameters if p in field_names)
    notes = {
        "quant.ts.ema": "Recursive and unadjusted. reset restarts on gaps; skip retains state; carry emits last warmed EMA.",
        "quant.stats.regression": "HAC assumes regular complete observations and a justified maximum autocovariance lag.",
        "quant.evaluate.forward_returns": "Future-dependent outputs may only be extracted through a research operation.",
        "quant.data.fingerprint": "Published metadata identity; this is not a fresh bytewise hash of every Parquet file.",
        "quant.performance.sharpe": "Descriptive annualization; autocorrelation requires a separate inference method.",
        "quant.portfolio.signed_notionals": "A time-varying equity input must carry decision and availability timestamps.",
    }
    fallback = "Applied only to declared inputs; relies on caller alignment and upstream availability for raw arrays."
    if category == "microstructure":
        fallback = "Aggregate bars contain no historical order book depth or executable quotes."
    complexity = (
        "O(T × S × W) worst case"
        if short in _EXPENSIVE
        else "O(T × S) for ordinary kernels; order statistics and per-row fitting may cost more"
        if streaming
        else "Depends on sample size, estimator or numerical solver"
    )
    params = {
        p.name: p
        for p in sig.parameters.values()
        if p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)
    }
    rules = {
        "window": "positive scheduled bar count or exact duration / interval_ms",
        "span": "positive scheduled bar count or exact duration / interval_ms",
        "interval_ms": "positive sampling interval in milliseconds when required",
        "min_periods": "positive finite-observation count; at most the rolling window",
        "gap": "reset, skip or carry",
        "axis": "valid dimension of the supplied NumPy array",
        "confidence": "open interval (0, 1)",
        "hac_lags": "nonnegative integer below complete sample size",
        "start": "UTC ISO timestamp or epoch milliseconds",
        "end": "exclusive UTC endpoint after start",
        "seed": "deterministic integer random seed",
        "levels": "ordered domain-specific thresholds",
    }
    defaults = {
        p.name: p.default
        if isinstance(p.default, (int, str, bool, float, tuple, type(None)))
        else "required"
        if p.default is inspect.Parameter.empty
        else _canonical(p.default)
        for p in params.values()
    }
    types = {
        p.name: (
            inspect.formatannotation(p.annotation)
            if p.annotation is not inspect.Parameter.empty
            else "duration str or int"
            if p.name in {"window", "span", "periods"}
            else "real array-like"
            if p.name
            in {
                "x",
                "y",
                "close",
                "open",
                "high",
                "low",
                "weights",
                "volume",
                "returns",
            }
            else type(p.default).__name__
            if p.default not in (None, inspect.Parameter.empty)
            else "see input contract"
        )
        for p in params.values()
    }
    constraints = {
        key: {
            "default": value,
            "rule": rules.get(key, "See definition and input contract."),
        }
        for key, value in defaults.items()
    }
    output = (
        "iterator"
        if inspect.isgeneratorfunction(function)
        else function.__name__
        if inspect.isclass(function)
        else "NumPy array or scalar"
        if category in {"math", "ts", "cs", "volatility", "signal"}
        else "See signature, definition and associated examples."
    )
    return dict(
        name=name,
        module=f"quant.{category}",
        category=category,
        signature=str(display),
        description=doc,
        mathematical_definition=definition,
        input_contract=inputs,
        required_fields=list(required),
        input_types=types,
        output_type=output,
        shape_semantics=shape,
        units=units,
        parameter_constraints=constraints,
        missing_data=missing,
        warmup=warm,
        causality=causal,
        streaming=streaming,
        complexity=complexity,
        limitations=notes.get(name, fallback),
        examples=[
            _EXAMPLES.get(f"{category}.{short}", f'q.registry.describe("{name}")')
        ],
        version="1",
        tests=(
            ["tests/test_sdk.py", "tests/test_sdk_integration.py"]
            if category in {"data", "features", "simulator", "validation"}
            else ["tests/test_sdk.py"]
        ),
    )


def _canonical(function):
    return f"{getattr(function, '__module__', '')}.{getattr(function, '__name__', '')}"


@lru_cache(maxsize=1)
def _functions():
    result = {}
    for category in _modules:
        m = import_module(f"quant.{category}")
        for short in [*m.__all__, *_TYPES.get(category, ())]:
            function = m
            for part in short.split("."):
                function = getattr(function, part)
            if callable(function):
                result[f"quant.{category}.{short}"] = function
    return result


@lru_cache(maxsize=None)
def _metadata(name):
    return _describe(name, _functions()[name])


def resolve(name):
    """Resolve an implemented canonical SDK function."""
    return _functions()[name]


def describe(name):
    """Return machine-readable metadata for one canonical function."""
    return json.loads(json.dumps(_metadata(name)))


def list_functions():
    """List all implemented exported function names in canonical order."""
    return sorted(_functions())


def list_by_category(category):
    """List canonical function names from one module category."""
    return [name for name in list_functions() if name.split(".")[1] == category]


def search(query):
    """Return all-term case-insensitive matches in names, definitions and field requirements."""
    terms = (
        re.sub(r"neutraliz(?:ation|e)", "neutral", query.lower())
        .replace("-", " ")
        .split()
    )
    result = []
    for name in list_functions():
        metadata = _metadata(name)
        category = metadata["category"]
        contents = " ".join(
            [
                name.replace("_", " "),
                metadata["description"],
                category,
                "cross sectional" if category == "cs" else "",
                metadata["mathematical_definition"],
                *metadata["required_fields"],
            ]
        ).lower()
        contents = contents.replace("-", " ").replace("–", " ")
        if all(word in contents for word in terms):
            result.append(name)
    return result


def generate_catalog(path):
    """Export the actual callable inventory as deterministic compact JSON."""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            [_metadata(name) for name in list_functions()],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return output


def generate_api(path):
    """Export signatures and contract summaries in Markdown from the implemented inventory."""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# SDK function reference",
        "",
        "Generated by `python -m quant catalog`. See [SDK.md](SDK.md) for workflows.",
        "",
    ]
    for category in _modules:
        lines.extend([f"## quant.{category}", ""])
        for name in list_by_category(category):
            info = _metadata(name)
            lines.extend(
                [
                    f"- `{name}{info['signature']}` — {info['description']} "
                    f"Streaming: {info['streaming'] or 'batch only'}."
                ]
            )
        lines.append("")
    output.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return output


__all__ = [
    "resolve",
    "describe",
    "list_functions",
    "list_by_category",
    "search",
    "generate_catalog",
    "generate_api",
]
