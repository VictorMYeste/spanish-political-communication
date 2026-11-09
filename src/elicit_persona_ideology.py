#!/usr/bin/env python3
"""Ask every persona the CIS left-right self-placement question (RQ2, RQ4).

Each persona of the reaction runs answers the CIS 1-10 left-right question in a
call of its own, with no post in it. The answer is used only to form left,
centre and right groups; it is never shown to the persona when it judges posts.
Design and wording: docs/PERSONA_IDEOLOGY.md.

The persona text must be exactly the one the persona had when it judged the
posts, so it is built by Candida's reaction script (--reactions-script), not
rebuilt here. --reactions-dir checks that every persona_sha256 matches the
reaction outputs.

A persona's text does not depend on the topic, so a persona selected for
several topics is asked once. The run resumes: personas already answered in the
output file are skipped. --dry-run prints one prompt and the number of calls.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import os
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REACTIONS_SCRIPT = "generate_spanish_counterfactual_reactions_v1_3_0.py"
DEFAULT_SELECTION = ROOT / "data" / "persona_selection_k50"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "persona_ideology"

PROMPT_VERSION = "persona-ideology-cis-v1"
FIELD = "left_right_self_placement"

SYSTEM_PROMPT = (
    "Role-play the person described in the supplied persona profile. "
    "Answer the survey question as that person would. "
    "Return only valid JSON with no markdown."
)

# CIS barometer wording (every wave since October 2023): "En general, cuando
# se habla de política se utilizan normalmente las expresiones izquierda y
# derecha. Situándonos en una escala que va del 1 al 10, en la que 1 significa
# 'lo más a la izquierda' y 10 'lo más a la derecha', ¿dónde se colocaría Ud.?"
# "spain" anchors the scale to Spanish politics, because most personas are not
# Spanish; "generic" is the literal translation, kept for the December check.
QUESTIONS = {
    "spain": (
        'In general, when talking about politics in Spain, the expressions "left" and '
        '"right" are normally used. On a scale from 1 to 10, where 1 means "the furthest '
        'to the left" and 10 means "the furthest to the right" of Spanish politics, '
        "where would you place yourself?"
    ),
    "generic": (
        'In general, when talking about politics, the expressions "left" and "right" '
        'are normally used. On a scale from 1 to 10, where 1 means "the furthest to the '
        'left" and 10 means "the furthest to the right", where would you place yourself?'
    ),
}

# Cut-offs of the left, centre and right groups, used by the summary printed at
# the end, by the RQ2 analysis (analyze_ideological_congruence.py) and, for CIS
# respondents, by the RQ4 analysis (analyze_cis_comparison.py).
GROUPS = {"left": range(1, 5), "centre": range(5, 7), "right": range(7, 11)}


def build_prompt(persona_text: str, frame: str) -> str:
    return (
        "[PERSONA PROFILE]\n"
        f"{persona_text}\n\n"
        "[SURVEY QUESTION]\n"
        f"{QUESTIONS[frame]}\n\n"
        "[OUTPUT JSON SCHEMA]\n"
        f'{{"{FIELD}": "integer from 1 to 10"}}'
    )


def parse_placement(text: str) -> Tuple[Optional[int], Optional[str]]:
    """The 1-10 placement in a model answer, or None and the reason."""
    value = (text or "").strip()
    if not value:
        return None, "empty_output"
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        first, last = value.find("{"), value.rfind("}")
        if first < 0 or last <= first:
            return None, "no_json_object"
        try:
            payload = json.loads(value[first : last + 1])
        except json.JSONDecodeError:
            return None, "invalid_json"
    if not isinstance(payload, dict) or FIELD not in payload:
        return None, f"missing_{FIELD}"
    raw = payload[FIELD]
    if isinstance(raw, bool):
        return None, f"not_an_integer: {raw!r}"
    try:
        number = float(str(raw).strip())
    except ValueError:
        return None, f"not_an_integer: {raw!r}"
    if not number.is_integer() or not 1 <= number <= 10:
        return None, f"out_of_range: {raw!r}"
    return int(number), None


def group_of(placement: int) -> str:
    return next(name for name, values in GROUPS.items() if placement in values)


def load_reactions_module(path: Path) -> ModuleType:
    if not path.exists():
        raise FileNotFoundError(
            f"Reaction script not found: {path}. Pass --reactions-script with the "
            "generator that produced the reactions."
        )
    spec = importlib.util.spec_from_file_location("spanish_reactions_generator", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def collect_personas(
    module: Any,
    framework: str,
    personas_path: Path,
    mft_responses: Optional[Path],
) -> List[Dict[str, Any]]:
    """One record per distinct persona text, built by the reaction script."""
    lookup = module.load_mft_responses_lookup(mft_responses) if framework == "mft" else {}
    rows = module.load_personas(
        personas_path,
        framework=framework,
        topics=module.TOPICS,
        max_rank=None,
        mft_responses_lookup=lookup,
    )
    by_id: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        seen = by_id.get(row["persona_id"])
        if seen is None:
            by_id[row["persona_id"]] = {
                "persona_id": row["persona_id"],
                "persona_sha256": row["persona_sha256"],
                "persona_text": row["persona_text"],
                "topics": [row["topic"]],
            }
        elif seen["persona_sha256"] != row["persona_sha256"]:
            raise ValueError(f"Persona {row['persona_id']} has different texts in different topics.")
        else:
            seen["topics"].append(row["topic"])
    return sorted(by_id.values(), key=lambda item: item["persona_id"])


def check_against_reactions(
    personas: Sequence[Dict[str, Any]],
    framework: str,
    reactions_dir: Path,
    allow_mismatch: bool = False,
) -> set:
    """IDs of the personas whose text differs from the one in the reaction outputs.

    Fails if any differs, unless allow_mismatch, because an answer given with
    another text belongs to another persona.
    """
    import pandas as pd

    files = sorted(reactions_dir.glob(f"*/*/{framework}/*/*/responses_active.csv"))
    if not files:
        raise FileNotFoundError(f"No {framework} responses_active.csv under {reactions_dir}")
    used = set()
    for path in files:
        frame = pd.read_csv(path, usecols=["persona_id", "persona_sha256"], dtype=str)
        used.update(zip(frame["persona_id"], frame["persona_sha256"]))
    asked = {(item["persona_id"], item["persona_sha256"]) for item in personas}
    absent = {pid for pid, _ in used} - {pid for pid, _ in asked}
    if absent:
        raise ValueError(f"{len(absent)} personas of the reactions are not in --personas, e.g. {sorted(absent)[:5]}")
    mismatched = {pid for pid, _ in asked - used}
    if not mismatched:
        print(f"[OK] {len(asked)} persona texts match the {framework} reactions ({len(files)} files).")
        return mismatched
    message = (
        f"{len(mismatched)} of {len(asked)} {framework} persona texts differ from the reactions: "
        f"{sorted(mismatched)}. Run with the persona file and reaction script that produced the reactions."
    )
    if not allow_mismatch:
        raise ValueError(message + " --allow-mismatch asks them anyway and marks them.")
    print("[WARNING] " + message + " They are asked and marked matches_reactions=False.")
    return mismatched


def read_records(path: Path) -> Dict[str, Dict[str, Any]]:
    """Latest record per persona_sha256 in a JSONL output."""
    records: Dict[str, Dict[str, Any]] = {}
    if not path.exists():
        return records
    for line in path.read_text(encoding="utf-8").split("\n"):
        if line.strip():
            record = json.loads(line)
            records[record["persona_sha256"]] = record
    return records


def ask(client: Any, args: argparse.Namespace, persona: Dict[str, Any]) -> Dict[str, Any]:
    prompt = build_prompt(persona["persona_text"], args.frame)
    raw, error, placement, attempts = "", None, None, 0
    for attempts in range(1, args.max_attempts + 1):
        try:
            response = client.chat.completions.create(
                model=args.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=args.temperature,
                max_tokens=args.max_tokens,
                seed=args.seed,
                response_format={"type": "json_object"},
            )
            raw = response.choices[0].message.content or ""
            placement, error = parse_placement(raw)
        except Exception as exc:  # network or server error: retry
            error = f"{type(exc).__name__}: {exc}"
        if placement is not None:
            break
        if attempts < args.max_attempts:
            time.sleep(min(2 ** (attempts - 1), 8))
    return {
        "framework": args.framework,
        "frame": args.frame,
        "persona_id": persona["persona_id"],
        "persona_sha256": persona["persona_sha256"],
        "matches_reactions": persona.get("matches_reactions"),
        "topics": ";".join(persona["topics"]),
        FIELD: placement,
        "status": "success" if placement is not None else "failed",
        "error": error if placement is None else None,
        "attempts": attempts,
        "raw_output": raw,
        "model": args.model,
        "prompt_version": PROMPT_VERSION,
        "temperature": args.temperature,
        "seed": args.seed,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def write_outputs(base: Path, records: Dict[str, Dict[str, Any]]) -> None:
    rows = sorted(records.values(), key=lambda row: row["persona_id"])
    columns = [key for key in rows[0] if key != "raw_output"]
    with base.with_suffix(".csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def print_summary(records: Dict[str, Dict[str, Any]]) -> None:
    values = [row[FIELD] for row in records.values() if row[FIELD] is not None]
    failed = len(records) - len(values)
    print(f"Answered: {len(values)}  failed: {failed}")
    if values:
        counts = Counter(values)
        print("Placement: " + "  ".join(f"{v}:{counts.get(v, 0)}" for v in range(1, 11)))
        groups = Counter(group_of(v) for v in values)
        print(
            "Provisional groups (1-4 / 5-6 / 7-10): "
            + "  ".join(f"{name} {groups.get(name, 0)} ({groups.get(name, 0) / len(values):.0%})" for name in GROUPS)
        )


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--framework", choices=["iw", "mft"], required=True)
    parser.add_argument("--frame", choices=sorted(QUESTIONS), default="spain",
                        help="Reference of the left-right scale (default: spain).")
    parser.add_argument("--personas", type=Path, default=None,
                        help="Persona selection CSV (default: data/persona_selection_k50/<framework>_selected_topk.csv).")
    parser.add_argument("--mft-responses", type=Path,
                        default=DEFAULT_SELECTION / "mft_selected_responses_k50_normalized.json",
                        help="MFQ-2 item responses, used by MFT only.")
    parser.add_argument("--reactions-script", type=Path, default=None,
                        help=f"Reaction generator that built the persona texts (default: src/ or candida_spanish_reactions/{DEFAULT_REACTIONS_SCRIPT}).")
    parser.add_argument("--reactions-dir", type=Path, default=None,
                        help="Reaction outputs (e.g. data/spanish_reactions) to check the persona texts against.")
    parser.add_argument("--allow-mismatch", action="store_true",
                        help="Ask personas whose text differs from the reactions anyway, marked matches_reactions=False.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--model", default="openai/gpt-oss-20b")
    parser.add_argument("--api-base", action="append", default=None,
                        help="OpenAI-compatible endpoint; repeat for several vLLM replicas.")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--concurrency", type=int, default=8, help="Requests in flight per endpoint.")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--max-attempts", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def default_reactions_script() -> Path:
    for folder in ("src", "candida_spanish_reactions"):
        path = ROOT / folder / DEFAULT_REACTIONS_SCRIPT
        if path.exists():
            return path
    return ROOT / "src" / DEFAULT_REACTIONS_SCRIPT


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    personas_path = args.personas or DEFAULT_SELECTION / f"{args.framework}_selected_topk.csv"
    module = load_reactions_module(args.reactions_script or default_reactions_script())
    personas = collect_personas(module, args.framework, personas_path, args.mft_responses)
    print(f"{args.framework}: {len(personas)} distinct personas from {personas_path}")
    if args.reactions_dir is not None:
        mismatched = check_against_reactions(personas, args.framework, args.reactions_dir, args.allow_mismatch)
        for persona in personas:
            persona["matches_reactions"] = persona["persona_id"] not in mismatched

    out_dir = args.output_dir / module.safe_slug(args.model)
    base = out_dir / f"{args.framework}_{args.frame}"
    jsonl = base.with_suffix(".jsonl")
    records = read_records(jsonl)
    for record in records.values():
        settings = (record["model"], record["prompt_version"], record["temperature"], record["seed"])
        if settings != (args.model, PROMPT_VERSION, args.temperature, args.seed):
            raise RuntimeError(f"{jsonl} holds answers with other settings {settings}; use another --output-dir.")
    pending = [p for p in personas if records.get(p["persona_sha256"], {}).get("status") != "success"]
    print(f"Already answered: {len(personas) - len(pending)}  calls to make: {len(pending)}")

    if args.dry_run:
        if personas:
            print("\n--- system ---\n" + SYSTEM_PROMPT)
            print("--- user (first persona) ---\n" + build_prompt(personas[0]["persona_text"], args.frame))
        return 0

    from openai import OpenAI

    api_key = os.environ.get(args.api_key_env, "EMPTY")
    bases = args.api_base or ["http://127.0.0.1:8000/v1"]
    clients = [OpenAI(base_url=b, api_key=api_key, timeout=args.timeout, max_retries=2) for b in bases]
    out_dir.mkdir(parents=True, exist_ok=True)
    with jsonl.open("a", encoding="utf-8") as handle, ThreadPoolExecutor(len(clients) * args.concurrency) as pool:
        futures = [pool.submit(ask, clients[i % len(clients)], args, p) for i, p in enumerate(pending)]
        for done, future in enumerate(as_completed(futures), start=1):
            record = future.result()
            records[record["persona_sha256"]] = record
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            if done % 50 == 0 or done == len(futures):
                print(f"{done}/{len(futures)}")

    if records:
        write_outputs(base, records)
        print(f"Wrote {jsonl} and {base.with_suffix('.csv')}")
        print_summary(records)
    return 0 if all(r["status"] == "success" for r in records.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
