"""plan_8_1 section 1.1: measure the output a paper read's two calls produce, and write their records.

Usage, inside the backend container, where the organization's model is configured:

    python -m app.cli.measure_read_budget --org-id <id> --doi <doi> --doi <doi> --doi <doi> \
        --max-tokens 64000 --out /tmp/read_measurements

plan_8_1 names the papers: SAMD1, Groff, and one multi-assay paper with more claims than either (their DOIs
are in HANDOFF, never in application code).

Each paper is read once through the prompts a read uses (the extraction, then the finding inventory over
the claims it returned), under a budget large enough that neither answer is cut off. Each call's output
tokens, stop reason and elapsed time are printed, and the two records (``extraction.json`` and
``inventory.json``) are written to ``--out`` for review and check-in under
``app/services/read_measurements/``. A cut-off answer is reported and sets no budget: raise
``--max-tokens`` and measure again.

Each run spends two model calls per paper on the organization's key.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from datetime import date as _date
from pathlib import Path

from app.services import validation_read_budget as budget
from app.services.llm_decision import decide, fenced_json

# The budget is the largest complete answer times this, rounded up, never below the starting budget.
HEADROOM = 1.5
ROUND_TO = 1000
STARTING_BUDGET = 16000
CUT_OFF = ("max_tokens", "length", "MAX_TOKENS")


def record_from(call: str, rows: list[dict], *, model: str, fingerprint: str, date: str) -> dict:
    """The measurement record for ``call`` from each paper's row. Raises when an answer was cut off,
    because a cut-off answer measures the budget it ran under, not a complete read."""
    cut = [r["paper"] for r in rows if r.get("stop_reason") in CUT_OFF or r.get("outcome") == "truncated"]
    if cut:
        raise ValueError(f"the {call} answer for {', '.join(cut)} was cut off; measure again with a larger budget")
    largest = max(int(r["output_tokens"]) for r in rows)
    with_headroom = int(math.ceil(largest * HEADROOM / ROUND_TO) * ROUND_TO)
    chosen = min(max(with_headroom, STARTING_BUDGET), budget.model_output_limit(model))
    rates = [
        r["output_tokens"] / r["elapsed_seconds"]
        for r in rows
        if r.get("output_tokens") and r.get("elapsed_seconds") and r["elapsed_seconds"] > 0
    ]
    return {
        "call": call,
        "fingerprint": fingerprint,
        "chosen_budget": chosen,
        "headroom": (
            f"{HEADROOM}x the largest complete answer ({largest} output tokens), rounded up to {ROUND_TO}, never "
            f"below the starting budget of {STARTING_BUDGET} and never above the model's documented maximum"
        ),
        "date": date,
        "status": "measured",
        "models": {
            model: {
                "papers": [
                    {
                        "paper": r["paper"],
                        "output_tokens": r["output_tokens"],
                        "elapsed_seconds": r.get("elapsed_seconds"),
                        "stop_reason": r.get("stop_reason"),
                        **({"claims": r["claims"]} if r.get("claims") is not None else {}),
                    }
                    for r in rows
                ],
                "tokens_per_second": round(min(rates), 1) if rates else None,
            }
        },
    }


def _row(paper: str, decision) -> dict:
    return {
        "paper": paper,
        "outcome": decision.outcome,
        "output_tokens": decision.output_tokens,
        "stop_reason": decision.stop_reason,
        "elapsed_seconds": decision.elapsed_seconds,
    }


async def measure_paper(paper: str, full_text: str, *, client, cfg, max_tokens: int) -> dict[str, dict]:
    """Each call's row for one paper: the extraction, then (when it answered) the inventory over its claims."""
    from app.services.validation_extraction_service import (
        PAPER_READING_INTENT,
        build_extraction_prompt,
        extraction_from,
    )
    from app.services.validation_inventory_stage import INVENTORY_INTENT, build_inventory_prompt

    system, payload = build_extraction_prompt(full_text)
    decision = await decide(
        intent=PAPER_READING_INTENT,
        system=system,
        payload=payload,
        client=client,
        model=cfg.model,
        api_key=cfg.api_key,
        max_tokens=max_tokens,
    )
    rows = {budget.EXTRACTION: _row(paper, decision)}
    data = (decision.data or fenced_json(decision.text) or None) if decision.ok else None
    if not isinstance(data, dict):
        return rows
    extraction = extraction_from(data, full_text=full_text)
    claims = extraction["claims"]
    rows[budget.EXTRACTION]["claims"] = len(claims)
    contrasts = (extraction.get("differential_design") or {}).get("contrasts") or []
    system, payload = build_inventory_prompt(
        full_text, claims=claims, experiments=extraction.get("reported_experiments") or [], contrasts=contrasts
    )
    decision = await decide(
        intent=INVENTORY_INTENT,
        system=system,
        payload=payload,
        client=client,
        model=cfg.model,
        api_key=cfg.api_key,
        max_tokens=max_tokens,
    )
    rows[budget.INVENTORY] = _row(paper, decision)
    return rows


async def _run(args) -> int:
    from app.database import async_session_factory
    from app.services import llm_provider_config_service
    from app.services.literature.fulltext_service import FullTextFetchService
    from app.services.llm_feature_models import FEATURE_LITERATURE_VALIDATION
    from app.services.llm_provider_clients import get_client

    async with async_session_factory() as session:
        cfg = await llm_provider_config_service.get_for_feature(session, args.org_id, FEATURE_LITERATURE_VALIDATION)
    if cfg is None:
        print("no language model is configured for literature validation in this organization", file=sys.stderr)
        return 2
    client = get_client(cfg.provider)
    measured: dict[str, list[dict]] = {budget.EXTRACTION: [], budget.INVENTORY: []}
    for doi in args.doi:
        text = await FullTextFetchService.fetch(doi=doi)
        if text is None:
            print(json.dumps({"paper": doi, "error": "Europe PMC has no full text for it"}), flush=True)
            return 3
        rows = await measure_paper(doi, text.text, client=client, cfg=cfg, max_tokens=args.max_tokens)
        for call, row in rows.items():
            print(json.dumps({"call": call, "model": cfg.model, "chars": len(text.text), **row}), flush=True)
            measured[call].append(row)
    fingerprints = {budget.EXTRACTION: budget.extraction_fingerprint(), budget.INVENTORY: budget.inventory_fingerprint()}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    status = 0
    for call, rows in measured.items():
        if len(rows) < len(args.doi):
            print(f"{call}: not every paper was measured; no record written", file=sys.stderr)
            status = 1
            continue
        try:
            record = record_from(
                call, rows, model=cfg.model, fingerprint=fingerprints[call], date=_date.today().isoformat()
            )
        except ValueError as exc:
            print(f"{call}: {exc}", file=sys.stderr)
            status = 1
            continue
        (out / f"{call}.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        print(f"{call}: wrote {out / f'{call}.json'} (chosen budget {record['chosen_budget']})", flush=True)
    return status


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--org-id", type=int, required=True)
    parser.add_argument("--doi", action="append", required=True, help="a paper to measure; give three or more")
    parser.add_argument("--max-tokens", type=int, default=64000, help="large enough that no answer is cut off")
    parser.add_argument("--out", default="/tmp/read_measurements")
    sys.exit(asyncio.run(_run(parser.parse_args(argv))))


if __name__ == "__main__":
    main()
