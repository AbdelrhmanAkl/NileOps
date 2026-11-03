"""Measure retrieval quality and abstention on knowledge/eval_questions.json.

    python -m scripts.evaluate_rag [--show-errors]

Reports (extractive mode by default; add --llm to test the local Qwen):
* top-1 policy accuracy and answer-contains-fact rate on answerable questions
* abstention rate on out-of-scope questions (should be ~100%)
Run it again after changing policies or thresholds. Synthetic questions are
easier than real employee questions: collect real ones before quoting numbers.
"""

import argparse
import json
from pathlib import Path

from src.core.config import PROJECT_ROOT, get_settings
from src.rag.answer import answer_question
from src.rag.chunking import read_policy_files
from src.rag.llm import get_llm_client
from src.rag.retrieval import IndexedChunk, PolicyIndex


def build_file_index() -> PolicyIndex:
    parsed = read_policy_files(get_settings().resolved_policies_dir)
    latest: dict[str, int] = {}
    for p in parsed:
        latest[p.policy_code] = max(latest.get(p.policy_code, 0), p.version)
    chunks = []
    for p in parsed:
        if p.version != latest[p.policy_code]:
            continue
        for c in p.chunks:
            chunks.append(IndexedChunk(len(chunks) + 1, p.policy_code, p.version, p.title, c.heading, c.text))
    return PolicyIndex(chunks)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--show-errors", action="store_true")
    parser.add_argument("--min-coverage", type=float, default=None)
    parser.add_argument("--llm", action="store_true", help="use the configured local LLM (LLM_PROVIDER=ollama)")
    parser.add_argument("--questions", type=Path, default=None, help="another questions JSON (same format)")
    args = parser.parse_args()
    settings = get_settings()
    min_cov = settings.rag_min_coverage if args.min_coverage is None else args.min_coverage
    data = json.loads(
        (args.questions or PROJECT_ROOT / "knowledge" / "eval_questions.json").read_text(encoding="utf-8")
    )
    llm = get_llm_client() if args.llm else None
    if args.llm and llm is None:
        print("LLM_PROVIDER غير مضبوط (none): سيتم التقييم بدون نموذج.")
    index = build_file_index()

    top1 = fact = answered = wrong = 0
    for item in data["answerable"]:
        res = answer_question(item["q"], index, top_k=settings.rag_top_k, min_coverage=min_cov, llm=llm, llm_min_coverage=settings.rag_llm_min_coverage)
        ok_policy = bool(res.citations) and res.citations[0].policy_code == item["policy"]
        ok_fact = res.status == "answered" and item.get("contains", "") in res.answer
        wrong += res.status == "answered" and not ok_policy
        answered += res.status == "answered"
        top1 += ok_policy
        fact += ok_fact
        if args.show_errors and not (ok_policy and ok_fact):
            first = res.citations[0].policy_code if res.citations else "-"
            print(f"MISS q={item['q']!r} got={first} status={res.status} cov={res.coverage}")
    n = len(data["answerable"])
    print(f"answerable: {n}  answered={answered}/{n}  top1_policy={top1}/{n}  fact_in_answer={fact}/{n}")
    print(f"WRONG-POLICY answers (answered but cited another policy; must be 0): {wrong}")

    abstained = 0
    for q in data["out_of_scope"]:
        res = answer_question(q, index, top_k=settings.rag_top_k, min_coverage=min_cov, llm=llm, llm_min_coverage=settings.rag_llm_min_coverage)
        abstained += res.status == "abstained"
        if args.show_errors and res.status != "abstained":
            print(f"SHOULD ABSTAIN q={q!r} cov={res.coverage} -> {res.citations[0].policy_code}")
    m = len(data["out_of_scope"])
    print(f"out-of-scope: {m}  abstained={abstained}/{m}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
