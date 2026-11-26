"""Review PH5-001 at durable inbox checkpoints and apply the frozen promotion rules."""

import argparse
import asyncio
from collections import defaultdict
import json

import aiohttp
import numpy as np
from scipy.stats import t

import quant as q
from examples.control_acceptance import wait_events
from quant.control_cli import request

from .campaign import NET_RULE, PORT, SCREEN_RULE, candidate, experiment, read, store


async def wait_for_outcomes(port, ids):
    url = f"http://127.0.0.1:{port}/api"
    done = set()

    def receive(rows):
        done.update(row["id"] for row in rows if row["id"] in ids and row["state"] in
                    {"completed", "failed", "cancelled", "blocked"})
        return done >= ids

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None)) as client:
        async with client.get(url + "/snapshot") as response:
            response.raise_for_status()
            snapshot = await response.json()
        if not receive(snapshot["hypotheses"]):
            async with client.get(url + f"/events?after={snapshot['cursor']}") as stream:
                stream.raise_for_status()
                await wait_events(stream, lambda delta: receive(delta["hypotheses"]))


def inbox_details(port, receipt):
    ids = set(receipt["submission_ids"])
    inbox = request(port, "/inbox?unreviewed=true")
    # The full inbox also makes checkpoints reproducible after acknowledgment.
    return {row["id"]: request(port, row["results_url"][4:])
            for row in (inbox if ids <= {i["id"] for i in inbox} else request(port, "/inbox"))
            if row["id"] in ids}


def screen_rows(details):
    by_configuration = defaultdict(dict)
    for h in details.values():
        for job in h["experiments"]:
            spec = job["specification"]
            family = h["family"]
            params = spec["parameters"]
            fold = spec["evaluation"]["fold"]
            outcome = job["outcome"] or {}
            by_configuration[(family, params["window"], params["threshold"])][fold] = {
                "hypothesis": h["id"], "job": job["id"], "status": job["status"],
                "metrics": outcome.get("metrics"), "failure": outcome.get("failure"),
                "batch": outcome.get("execution", {}).get("batch_id"),
            }
    rows = [{"family": fam, "window": n, "threshold": k, "folds": folds}
            for (fam, n, k), folds in sorted(by_configuration.items())]

    def eligible(m):
        return (m is not None and m["mean_ic"] is not None
                and m["mean_ic"] >= SCREEN_RULE["min_ic"]
                and m["coverage"] is not None and m["coverage"] >= SCREEN_RULE["min_feature_coverage"]
                and m["ic_observations"] >= SCREEN_RULE["min_ic_observations"])

    qualified = [r for r in rows if set(r["folds"]) == {"A", "B"}
                 and all(eligible(x["metrics"]) for x in r["folds"].values())]
    ordered = sorted(qualified, key=lambda r: (-min(x["metrics"]["mean_ic"] for x in r["folds"].values()),
                                              r["family"], r["window"], r["threshold"]))
    selected = {}
    for row in ordered:
        if len(selected) == SCREEN_RULE["max_families"]:
            break
        selected.setdefault(row["family"], row)
    return rows, ordered, list(selected.values())


def native_campaign(plan, rows, fold):
    hypotheses = []
    for row in rows:
        for model, costs in (("base", plan["protocols"][fold]["costs"]), ("stress", plan["stress_cost"])):
            spec = plan["protocols"][fold]
            bound = spec | {"costs": costs, "evaluation": spec["evaluation"] | {
                "cost_model": model, "screen_evidence": {k: v["job"] for k, v in row["folds"].items()},
            }}
            h = experiment(row["family"], fold, "native", bound, [
                candidate(row["family"], row["window"], row["threshold"], "native")
            ])
            h.update(key=f"{h['key']}-{model}", name=f"{h['name']} / {model}")
            hypotheses.append(h)
    return {"campaign": f"PH5-001 / {fold}", "hypotheses": hypotheses}


def summarize_native(job):
    spec = job["specification"]
    metrics = (job["outcome"] or {}).get("metrics")
    summary = {"job": job["id"], "status": job["status"], "p": 1.0, "usable": False,
               "model": spec["evaluation"]["cost_model"]}
    if not metrics:
        return summary
    for key in ("net_pnl", "gross_pnl_before_execution_cost_fees_and_funding", "execution_costs",
                "fills", "orders", "indicative_equity_warning", "sharpe", "maximum_drawdown",
                "funding_warnings", "mark_warnings"):
        summary[key] = metrics[key]
    daily = metrics["daily_marked_equity"]
    expected = list(range(spec["start_ms"], spec["end_ms"], 86_400_000))
    if [ms for ms, _ in daily] != expected or metrics["indicative_equity_warning"]:
        return summary
    pnl = np.diff([spec["costs"]["initial_wallet"], *[v for _, v in daily]])
    if not np.isfinite(pnl).all():
        return summary
    bp = pnl / NET_RULE["gross_usdt"] * 10_000
    model = q.stats.regression(bp, np.empty((len(bp), 0)), hac_lags=NET_RULE["hac_lags"])
    se = float(model.standard_errors[0])
    mean = float(bp.mean())
    positive = np.maximum(pnl, 0)
    share = float(positive.max() / positive.sum()) if positive.sum() else 1.0
    floor = NET_RULE["daily_bps_on_gross_floor"]
    summary.update(usable=True, daily_bps=mean, top_day_share=share, daily_pnl=pnl.tolist(),
                   p=float(t.sf((mean - floor) / se, len(pnl) - 1)) if np.isfinite(se) and se > 0 else 1.0)
    return summary


def mark(port, detail, verdict, note):
    key = detail["id"]
    request(port, f"/hypotheses/{key}/verdict", {"verdict": verdict, "note": note})
    updated = request(port, f"/hypotheses/{key}")
    request(port, f"/hypotheses/{key}/acknowledge", {"revision": updated["revision"]})


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("stage", choices=("screen", "validation", "confirmation"))
    cli.add_argument("--port", type=int, default=PORT)
    cli.add_argument("--apply", action="store_true", help="Record outcomes and submit the prescribed next stage")
    cli.add_argument("--events", action="store_true", help="Use SSE to wait for a complete checkpoint")
    args = cli.parse_args()
    port, stage = args.port, args.stage
    receipt = read(("initial" if stage == "screen" else stage) + "-receipt")
    if args.events:
        asyncio.run(wait_for_outcomes(port, set(receipt["submission_ids"])))
    details = inbox_details(port, receipt)
    if len(details) != len(receipt["submission_ids"]):
        print(f"Checkpoint ready for {len(details)} of {len(receipt['submission_ids'])} hypotheses")
        return
    store(stage + "-registry-results", details)
    plan = read("frozen-evaluation")
    if stage == "screen":
        rows, eligible, selected = screen_rows(details)
        store("screen-decision", {"rows": rows, "eligible": eligible, "selected": selected})
        print(json.dumps({"configurations": len(rows), "eligible": len(eligible), "selected": selected}))
        by_family = {x["family"] for x in selected}
        verdicts = {h["id"]: ("promising" if h["family"] in by_family else
                    "rejected" if h["state"] == "completed" else "inconclusive") for h in details.values()}
        note = "PH5-001 paired-fold descriptive screening gate and five-family cap. See the campaign decision record."
        next_stage, promote = "validation", selected
    else:
        families = defaultdict(dict)
        for h in details.values():
            job, = h["experiments"]
            summary = summarize_native(job)
            families[h["family"]][summary["model"]] = summary | {"hypothesis": h["id"]}
        names = sorted(families)
        adjusted = q.validation.adjust_pvalues([families[name]["base"]["p"] for name in names])
        winners = []
        verdicts = {}
        for name, p in zip(names, adjusted):
            base, stress = (families[name][m] for m in ("base", "stress"))
            passed = (base["usable"] and stress["usable"] and
                      base["daily_bps"] > NET_RULE["daily_bps_on_gross_floor"] and stress["net_pnl"] > 0
                      and base["top_day_share"] <= NET_RULE["max_top_day_share_positive_pnl"]
                      and (stage == "confirmation" or p < NET_RULE["holm_alpha"]))
            if passed:
                winners.append(name)
            families[name].update(holm_p=float(p) if stage == "validation" else None, passed=passed)
            verdict = "promising" if passed else "rejected" if base["usable"] and stress["usable"] else "inconclusive"
            verdicts.update({base["hypothesis"]: verdict, stress["hypothesis"]: verdict})
        store(stage + "-decision", {"families": families, "passed": winners})
        print(json.dumps({"families": families, "passed": winners}))
        next_stage = "confirmation" if stage == "validation" else None
        promote = [r for r in read("screen-decision")["selected"] if r["family"] in winners]
        note = f"PH5-001 {stage} net economics gate. See the campaign decision record for costs and inference."
    if args.apply:
        if next_stage and promote:
            payload = native_campaign(plan, promote, next_stage)
            store(next_stage + "-specification", payload)
            result = request(port, "/hypotheses", payload)
            store(next_stage + "-receipt", result)
            print(json.dumps(result))
        for key, verdict in verdicts.items():
            if details[key]["verdict"] != verdict:
                mark(port, details[key], verdict, note)


if __name__ == "__main__":
    main()
