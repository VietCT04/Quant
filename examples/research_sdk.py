"""Run ten development-only research demonstrations using the same public SDK."""

import argparse
import json
from pathlib import Path
from uuid import uuid4

import numpy as np

import quant as q

CASES = (
    "ema",
    "shock",
    "imbalance",
    "momentum",
    "volatility",
    "quantiles",
    "decay",
    "simulation",
    "sweep",
    "request",
)


def _score(block):
    close = block.values["close"]
    return q.ts.log_returns(close, 12)


def _simulation(selection, out):
    from simulator.native import Settings

    fast = q.features.expression("quant.ts.ema", "close", span=12, units="USDT/base")
    slow = q.features.expression("quant.ts.ema", "close", span=60, units="USDT/base")
    blocks = q.features.evaluate(
        q.data.iter_blocks(selection), {"fast": fast, "slow": slow}
    )
    target_stream = (
        q.simulator.targets(
            block, 100 * q.math.sign(block.values["fast"] - block.values["slow"])
        )
        for block in blocks
    )
    result = q.simulator.run(
        selection,
        target_stream,
        Settings(rebalance_ticks=12, snapshot_ticks=60),
        out=out,
    )
    return {
        "report": str(out / "report.json"),
        "orders": result.report["orders"],
        "funding_warning_count": len(result.report["funding_warnings"]),
    }


def run(case, selection, destination):
    db = destination / "experiments.sqlite"
    if case == "request":
        spec = q.requests.Request(
            name="historical_order_book_depth",
            module="quant.microstructure",
            purpose="Measure observed depth when suitable archives are licensed and available.",
            required_inputs=("quoted_size_by_level",),
            parameters={"levels": "positive integer"},
            causal=True,
            streaming_required=True,
            acceptance_criteria=(
                "Reference snapshot agreement",
                "Availability and missing-coverage tests",
                "Stream benchmark",
            ),
            output_units="base",
            missing_data="Unknown levels remain missing",
        )
        return {
            "capability_exists": q.requests.check(
                "quant.microstructure.historical_order_book_depth"
            ),
            "request_id": q.requests.submit(db, spec),
        }
    if case == "simulation":
        return _simulation(selection, destination / "simulation")
    block = next(q.data.iter_blocks(selection, block_steps=4096))
    values = block.values
    close = values["close"]
    if case == "ema":
        trend = q.ts.ema(close, 12) - q.ts.ema(close, 60)
        return {"positive_trend_observations": int(np.sum(trend > 0))}
    if case == "shock":
        return q.evaluate.event_study(
            np.abs(q.ts.log_returns(close)) > 0.001, block, [1, 12, 60]
        )
    if case == "imbalance":
        x = q.microstructure.volume_imbalance(
            values["buy_volume"], values["sell_volume"]
        )
        return {
            "signal_coverage": float(
                q.evaluate.signal_coverage(x, active=block.status <= 1)
            ),
            "sample_size": int(np.isfinite(x).sum()),
        }
    if case == "momentum":
        return {
            "cross_sectional_rank_last": q.cs.rank(
                _score(block), active=block.status <= 1
            )[-1].tolist()
        }
    if case == "volatility":
        volatility = q.volatility.realized_volatility(q.ts.log_returns(close), 60)
        weight = q.portfolio.inverse_volatility(
            volatility, direction=q.math.sign(_score(block))
        )
        target = q.portfolio.signed_notionals(weight, 200.0)
        return {"last_usdt_targets": dict(zip(block.symbols, target[-1].tolist()))}
    if case == "quantiles":
        labels = q.evaluate.forward_returns(block, 12)
        averages, counts = q.evaluate.quantile_returns(_score(block), labels, groups=3)
        return {
            "observations": counts.sum(axis=0).tolist(),
            "mean_forward_return": np.nanmean(averages, axis=0).tolist(),
        }
    if case == "decay":
        return q.evaluate.alpha_decay(_score(block), block, [1, 12, 60])
    if case == "sweep":
        candidates = list(q.optimize.grid_search({"span": [12, 24, 60]}))
        experiments = []
        for params in candidates:
            identity = q.experiment.register(
                db,
                hypothesis="Demonstrate the registry using finite output coverage for predeclared EMA settings.",
                parameters=params,
                start=selection.start,
                end=selection.end,
                universe=selection.symbols,
                dataset_fingerprint=q.data.fingerprint(selection),
                seed=0,
                features=["quant.ts.ema"],
                evaluation={
                    "metric": "finite observation coverage",
                    "block_rows": len(close),
                },
                costs={"simulator": False},
                candidate_count=len(candidates),
            )
            q.experiment.run(
                db,
                identity,
                lambda spec: {
                    "metrics": {
                        "coverage": float(
                            q.evaluate.signal_coverage(
                                q.ts.ema(close, spec["parameters"]["span"])
                            )
                        )
                    }
                },
            )
            experiments.append(identity)
        return q.experiment.compare(db, experiments, metrics=["coverage"])
    raise ValueError(case)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("case", choices=(*CASES, "all"))
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("bench-data") / f"sdk-example-{uuid4().hex[:8]}",
    )
    cli = parser.parse_args()
    cli.out.mkdir(parents=True, exist_ok=True)
    dataset = q.data.open()
    selection = q.data.select(
        dataset,
        start="2026-06-10T00:00:00+00:00",
        end="2026-06-10T01:00:00+00:00",
        symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "XRPUSDT"],
        fields=("close", "buy_volume", "sell_volume"),
    )
    result = {
        case: run(case, selection, cli.out)
        for case in (CASES if cli.case == "all" else [cli.case])
    }
    output = json.dumps(q.experiment._plain(result), indent=2, allow_nan=False)
    (cli.out / "summary.json").write_text(output, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
