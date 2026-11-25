"""Deterministic Phase 4 acceptance campaign on a fixed development slice."""

import argparse
from dataclasses import asdict
from pathlib import Path

import numpy as np

from marketdata.model import write_json
from simulator.native import Settings

import quant as q


def _pair(params):
    return {
        name: q.features.expression(
            "quant.ts.ema", "close", span=params[name], units="USDT/base"
        )
        for name in ("fast", "slow")
    }


def _score(values):
    return q.math.safe_divide(values["fast"] - values["slow"], values["close"])


def _target(block):
    return np.sign(_score(block.values)) * 300.0


def _comparison(first, second):
    ignore = {"mode", "settings", "closed_reductions"}
    return {k: v for k, v in first.items() if k not in ignore} == {
        k: v for k, v in second.items() if k not in ignore
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--start", default="2026-06-10")
    p.add_argument("--end", default="2026-06-11")
    p.add_argument("--interrupt-only", action="store_true")
    args = p.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    db = args.out / "research.sqlite"
    d = q.data.select(q.data.open(), start=args.start, end=args.end, fields=("close",))
    config = Settings(rebalance_ticks=12)
    candidates = [{"fast": f, "slow": s} for f in range(5, 15) for s in range(20, 30)]
    common = dict(
        start=d.start,
        end=d.end,
        universe=d.symbols,
        dataset_fingerprint=q.data.fingerprint(d),
        seed=0,
        features=["quant.ts.ema"],
        costs=asdict(config),
    )
    ids = q.research.submit(
        db,
        [{"kind": "screen", "parameters": params} for params in candidates],
        **common,
        hypothesis="Verify shared features and label diagnostics on a predeclared grid.",
        evaluation={"scope": "development infrastructure", "horizon": 12, "groups": 3},
    )
    before = q.research.status(db, ids=ids)
    if not before["attempt_history"]:
        try:

            def interrupt(jobs):
                raise KeyboardInterrupt("Acceptance fixture interruption")

            q.research.run(db, {"screen": interrupt}, claim_size=8, max_batches=1)
        except KeyboardInterrupt:
            pass
        before = q.research.status(db, ids=ids)
    interrupted = sum(
        (a["outcome"] or {}).get("failure", {}).get("type") == "KeyboardInterrupt"
        for a in before["attempt_history"]
        if (a["outcome"] or {}).get("failure")
    )
    if interrupted != 8:
        raise RuntimeError("The interrupted screen wave was not retained")
    if args.interrupt_only:
        print(
            f"Persisted {interrupted} interrupted attempts and {before['counts']['queued']} queued jobs"
        )
        return

    profiles = {}

    def do_screen(jobs):
        q.research.verify_selection(jobs, d)
        values = [
            q.evaluate.ScreenCandidate(
                j["id"], _pair(j["specification"]["parameters"]), _score
            )
            for j in jobs
        ]
        result = q.evaluate.screen(
            d,
            values,
            workers=args.workers,
            groups=3,
            event_threshold=0.001,
            method="pearson",
        )
        sequential = q.evaluate.screen(
            d, values, workers=1, groups=3, event_threshold=0.001, method="pearson"
        )
        if (
            result["failures"]
            or sequential["failures"]
            or result["results"] != sequential["results"]
        ):
            raise RuntimeError(
                "Diagnostic execution failed or disagreed with sequential reference"
            )
        profiles.update(
            parallel_screen=result["profile"], sequential_screen=sequential["profile"]
        )
        return {
            name: {"metrics": metrics} for name, metrics in result["results"].items()
        }

    q.research.run(db, {"screen": do_screen}, claim_size=100)
    if q.research.status(db, ids=ids)["counts"]["completed"] != 100:
        raise RuntimeError(
            "The recovered campaign did not finish all existing screen jobs"
        )
    # Replay the first eight predeclared configurations regardless of observed diagnostics.
    native_ids = q.research.submit(
        db,
        [
            {"kind": "native", "parameters": params, "depends_on": [screen_id]}
            for params, screen_id in zip(candidates[:8], ids[:8])
        ],
        **common,
        hypothesis="Verify causal native accounting and summary replay for eight fixed candidates.",
        evaluation={"scope": "development infrastructure", "rebalance_ticks": 12},
    )

    def as_candidate(identity, params):
        return q.simulator.Candidate(identity, _target, _pair(params), config=config)

    def do_native(jobs):
        q.research.verify_selection(jobs, d)
        values = [as_candidate(j["id"], j["specification"]["parameters"]) for j in jobs]
        shared = q.simulator.run_batch(
            d,
            values,
            out=args.out / "summary",
            workers=args.workers,
            cache_directory=args.out / "market",
            resume_token="phase4-native-fixture-v1",
        )
        serial = q.simulator.run_batch(
            d,
            values,
            out=args.out / "serial",
            workers=1,
            cache_directory=args.out / "market",
            resume_token="phase4-native-fixture-v1",
        )
        if (
            shared.failures
            or serial.failures
            or any(
                shared.results[key].report != serial.results[key].report
                for key in shared.results
            )
        ):
            raise RuntimeError(
                "Native shared execution failed or disagreed with serial replay"
            )
        profiles.update(
            native_parallel=shared.profile, native_sequential=serial.profile
        )
        return {
            name: {"metrics": item.report, "artifacts": [str(item.report_path)]}
            for name, item in shared.results.items()
        }

    final = q.research.run(db, {"native": do_native})
    if final["counts"]["completed"] != 108:
        raise RuntimeError("Native jobs were not all recorded")
    key = native_ids[0]
    registered = q.experiment.reproduce(db, key)
    audit_path = args.out / "audit" / key / "report.json"
    if audit_path.is_file():
        from marketdata.model import load_json

        audited = load_json(audit_path)
    else:
        audit = q.simulator.run_batch(
            d,
            [as_candidate(key, registered["specification"]["parameters"])],
            out=args.out / "audit",
            logging="audit",
            workers=1,
            cache_directory=args.out / "market",
        )
        if audit.failures:
            raise RuntimeError(audit.failures)
        audited = audit.results[key].report
        profiles["audit"] = audit.profile
    if not _comparison(registered["outcome"]["metrics"], audited):
        raise RuntimeError("Full audit replay changed required summary metrics")
    write_json(
        args.out / "acceptance.json",
        {
            "counts": final["counts"],
            "screen_candidates": 100,
            "native_candidates": 8,
            "interrupted_attempts": interrupted,
            "replay_experiment": key,
            "screening_sequential_equal": True,
            "native_sequential_equal": True,
            "audit_summary_equal": True,
            "profiles": profiles,
            "data_range_ms": [d.start, d.end],
            "selection_bias": "Liquid 50 frozen in November 2026",
            "database": str(db),
        },
    )
    print(f"Completed {final['counts']} with audited replay {key}")


if __name__ == "__main__":
    main()
