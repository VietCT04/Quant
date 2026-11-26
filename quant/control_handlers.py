"""Trusted, data-only handlers for the local control center."""

from dataclasses import fields
from functools import partial
from uuid import uuid4

import numpy as np

from simulator.native import Settings

from . import data, evaluate, features, math, research, simulator
from .experiment import _dump, _git

KINDS = frozenset({"screen", "native", "audit"})
SCREEN_OPTIONS = {"horizon", "method", "groups", "event_threshold"}
SETTINGS = {field.name for field in fields(Settings)} - {"lag", "interval", "logging"}


def _expressions(parameters):
    if "expressions" not in parameters:
        fast, slow = parameters["fast"], parameters["slow"]
        if type(fast) is not int or type(slow) is not int or not 1 <= fast < slow <= 100_000:
            raise ValueError("EMA candidates need integer spans with 1 <= fast < slow <= 100000")
        return {
            name: features.expression("quant.ts.ema", "close", span=value, units="USDT/base")
            for name, value in (("fast", fast), ("slow", slow))
        }

    def node(value, depth=0):
        if value == "close":
            return value
        if depth >= 8 or not isinstance(value, dict) or set(value) - {"name", "inputs", "units", "parameters"}:
            raise ValueError("Use bounded, registered causal feature expressions over close")
        inputs = value["inputs"]
        if not isinstance(inputs, list) or not 1 <= len(inputs) <= 4:
            raise ValueError("Feature inputs must be a list with one to four entries")
        return features.expression(
            value["name"], *(node(x, depth + 1) for x in inputs),
            units=value["units"], **value.get("parameters", {}),
        )

    payload = parameters["expressions"]
    if not isinstance(payload, dict) or set(payload) != {"fast", "slow"}:
        raise ValueError("Provide two comparable expressions named fast and slow")
    values = {name: node(value) for name, value in payload.items()}
    if any(isinstance(value, str) for value in values) or values["fast"].units != values["slow"].units:
        raise ValueError("Signal expressions must be feature nodes with matching units")
    return values


def _score(values, normalize):
    spread = values["fast"] - values["slow"]
    return math.safe_divide(spread, values["close"]) if normalize else spread


def _target(block, notional, direction):
    return np.sign(_score(block.values, False)) * (notional * direction)


def validate(candidate, spec):
    if not isinstance(candidate, dict) or set(candidate) != {"kind", "parameters"}:
        raise ValueError("Candidates must contain kind and parameters")
    kind = candidate["kind"]
    if not isinstance(kind, str) or kind not in KINDS:
        raise ValueError("Unknown trusted handler kind")
    params = candidate["parameters"]
    if not isinstance(params, dict) or params.keys() - {"fast", "slow", "expressions", "notional", "direction", "normalize", "settings"}:
        raise ValueError("Unknown candidate parameter")
    if ("expressions" in params) == bool({"fast", "slow"} & params.keys()):
        raise ValueError("Specify EMA spans or two feature expressions")
    _expressions(params)
    if type(params.get("direction", 1)) is not int or params.get("direction", 1) not in {-1, 1}:
        raise ValueError("Direction must be -1 or 1")
    notional = params.get("notional", 300)
    if type(notional) not in {float, int} or not 0 < notional <= 1e9:
        raise ValueError("Notional must be positive and at most 1 billion USDT")
    if type(params.get("normalize", True)) is not bool:
        raise ValueError("normalize must be boolean")
    overrides = params.get("settings", {})
    if not isinstance(overrides, dict) or overrides.keys() - SETTINGS:
        raise ValueError("Unknown native settings override")
    costs = spec["costs"]
    if not isinstance(costs, dict) or costs.keys() - SETTINGS:
        raise ValueError("Unknown native cost setting")
    if type(spec["evaluation"].get("prepared_cache", False)) is not bool:
        raise ValueError("prepared_cache must be boolean")
    for name, val in (costs | overrides).items():
        if type(val) not in {int, float, bool} or not np.isfinite(val):
            raise ValueError(f"Invalid setting value for {name}")
    Settings(**(costs | overrides))
    evaluation = spec["evaluation"]
    if kind == "screen":
        groups, horizon = evaluation.get("groups", 0), evaluation.get("horizon", 12)
        method, threshold = evaluation.get("method", "spearman"), evaluation.get("event_threshold")
        if type(horizon) is not int or horizon < 1 or type(groups) is not int or groups < 0 or groups == 1 or groups > 100:
            raise ValueError("Screening needs a positive horizon and zero or 2 to 100 groups")
        if method not in {"spearman", "pearson"} or threshold is not None and (
            type(threshold) not in {int, float} or not np.isfinite(threshold) or threshold < 0
        ):
            raise ValueError("Invalid screening method or event threshold")


def batch_key(job):
    spec = job["specification"]
    evaluation = spec["evaluation"]
    if spec["job_kind"] == "screen":
        options = {key: evaluation[key] for key in SCREEN_OPTIONS if key in evaluation}
    else:
        options = {"prepared_cache": evaluation.get("prepared_cache", False)}
    if spec["job_kind"] == "audit":
        return job["id"]
    return _dump([*(spec[k] for k in ("start_ms", "end_ms", "universe", "dataset_fingerprint")), options])


def execute(kind, jobs, *, source, root, budgets, control):
    first = jobs[0]["specification"]
    selection = data.select(
        data.open(source), start=first["start_ms"], end=first["end_ms"], symbols=first["universe"],
    )
    research.verify_selection(jobs, selection)
    evaluation = first["evaluation"]
    provenance = {"git": _git(), "handler": kind, "handler_version": 1}
    output = root / "artifacts" / uuid4().hex
    limits = {key: budgets[key] for key in ("cpu_limit", "memory_budget_gib")}
    control.start(jobs, selection)
    if kind == "screen":
        candidates = []
        for job in jobs:
            params = job["specification"]["parameters"]
            validate({"kind": kind, "parameters": params}, job["specification"])
            scorer = partial(_score, normalize=params.get("normalize", True))
            if params.get("direction", 1) == -1:
                def inverted(values, scorer=scorer):
                    return -scorer(values)
                scorer = inverted
            candidates.append(evaluate.ScreenCandidate(job["id"], _expressions(params), scorer))
        options = {key: evaluation[key] for key in SCREEN_OPTIONS if key in evaluation}
        result = evaluate.screen(selection, candidates, **limits, **options, cancel=control)
        metrics, failures, profile = result["results"], result["failures"], result["profile"]
        artifacts = {}
    else:
        candidates = []
        for job in jobs:
            spec = job["specification"]
            params = spec["parameters"]
            validate({"kind": kind, "parameters": params}, spec)
            settings = Settings(**(spec["costs"] | params.get("settings", {})))
            target = partial(_target, notional=params.get("notional", 300), direction=params.get("direction", 1))
            candidates.append(simulator.Candidate(job["id"], target, _expressions(params), config=settings))
        result = simulator.run_batch(
            selection, candidates, out=output, **limits, disk_budget_gib=budgets["disk_budget_gib"],
            logging="audit" if kind == "audit" else "summary",
            cache_directory=root / "cache" if evaluation.get("prepared_cache") else None,
            batch_size=1 if kind == "audit" else 128, cancel=control,
        )
        metrics = {job: item.report for job, item in result.results.items()}
        artifacts = {
            job: [str(item.report_path)] + (
                [str(path) for path in sorted(item.report_path.parent.glob("*.parquet"))]
                if kind == "audit" else []
            )
            for job, item in result.results.items()
        }
        failures, profile = result.failures, result.profile
    control.finish(profile)
    provenance["batch_id"] = control.batch_id
    return {
        job: {"metrics": value, "artifacts": artifacts.get(job, []), "execution": provenance}
        for job, value in metrics.items()
    } | {
        job: {"failure": {"type": "HandlerError", "message": failure}, "execution": provenance}
        for job, failure in failures.items()
    }
