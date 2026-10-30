#!/usr/bin/env python3
"""Full LLM judge scoring for LoCoMo benchmark via TokenHarbor.

Uses generative QA: generates answers from retrieved context, then judges
correctness against gold answers. This matches competitor evaluation methods.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path("/content/weft")

def run(cmd: str, check: bool = True) -> str:
    print(f"[setup] {cmd}", flush=True)
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and r.returncode != 0:
        print(f"[setup] FAILED: {cmd}\n{r.stderr}", flush=True)
        sys.exit(r.returncode)
    out = r.stdout.strip()
    if out:
        print(out[:500], flush=True)
    return out

if not REPO.exists():
    run("git clone https://github.com/ousiaresearch/weft.git /content/weft")
os.chdir(REPO)
sys.path.insert(0, str(REPO))

run("pip install -e . -q")
run("pip install datasets pytest -q")
run("pip install requests -q")
run("pip install sentence-transformers -q")

import requests  # noqa: E402

# TokenHarbor config
TOKEN = os.environ.get("TOKENHARBOR_API_KEY", "")
if not TOKEN:
    token_path = Path.home() / ".config/vireth/th_token"
    if token_path.exists():
        TOKEN = token_path.read_text().strip()

if not TOKEN:
    print("[ERROR] No TokenHarbor token found", flush=True)
    sys.exit(1)

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json"
}
JUDGE_MODEL = "deepseek-v4.1-flash"
BASE_URL = "https://tokenharbor.ai/v1"

print(f"[setup] TokenHarbor token loaded (length {len(TOKEN)})", flush=True)
print(f"[setup] Judge model: {JUDGE_MODEL}", flush=True)

# Verify judge model works
print("[setup] Testing judge model...", flush=True)
test_resp = requests.post(
    f"{BASE_URL}/chat/completions",
    headers=HEADERS,
    json={
        "model": JUDGE_MODEL,
        "messages": [{"role": "user", "content": "Say 'ok'"}],
        "max_tokens": 10
    }
)
print(f"[setup] Judge test: {test_resp.status_code}", flush=True)
if test_resp.status_code != 200:
    print(f"[setup] Judge test failed: {test_resp.text[:200]}", flush=True)
    sys.exit(1)

# Load embedder for hybrid recall
print("[setup] Loading sentence-transformers embedder...", flush=True)
from sentence_transformers import SentenceTransformer  # noqa: E402

class STEmbedder:
    """Wrapper for sentence-transformers to match Weft's embedder interface."""
    def __init__(self, model_name: str = "BAAI/bge-m3"):
        self.model = SentenceTransformer(model_name)
        self.model_id = f"sentence-transformers/{model_name}"
        self._dim = self.model.get_sentence_embedding_dimension()
    
    def embed_query(self, text: str) -> list[float]:
        return self.model.encode(f"query: {text}", normalize_embeddings=True).tolist()
    
    def embed_document(self, text: str) -> list[float]:
        return self.model.encode(text, normalize_embeddings=True).tolist()
    
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.model.encode(texts, normalize_embeddings=True).tolist()

try:
    embedder = STEmbedder("BAAI/bge-m3")
    print(f"[setup] Embedder loaded (dim={embedder._dim})", flush=True)
except Exception as e:
    print(f"[setup] Failed to load BAAI/bge-m3: {e}", flush=True)
    print("[setup] Falling back to lexical-only recall", flush=True)
    embedder = None

from benchmarks.full.locomo import _load_dataset, convert_sample  # noqa: E402
from benchmarks.full.common import make_engine, make_ctx, create_substrate, run_recall  # noqa: E402
from weft.scope import ScopeEngine  # noqa: E402
from weft.extraction import extract_facts, write_claims  # noqa: E402

print("\n[run] Loading LoCoMo dataset...", flush=True)
ds = _load_dataset()
print(f"[run] Loaded {len(ds)} conversations", flush=True)

# Generative QA prompt
QA_PROMPT = """Answer the question based ONLY on the provided context. If the context does not contain sufficient information to answer the question, respond with "I don't know."

Context:
{context}

Question: {question}

Answer:"""

# Judge prompt
JUDGE_PROMPT = """You are an impartial judge evaluating whether a generated answer correctly addresses a question based on the provided context.

Question: {question}

Gold Answer: {gold_answer}

Generated Answer: {generated_answer}

Instructions:
- Determine if the generated answer is correct given the context and gold answer.
- Consider paraphrases, synonyms, and equivalent expressions as correct.
- Answer with ONLY a JSON object: {{"correct": true/false, "score": 0.0-1.0, "reason": "brief explanation"}}
- "correct": true if the generated answer matches the gold answer, false otherwise.
- "score": 1.0 if fully correct, 0.0 if completely incorrect, or a fractional value for partial matches.
"""

def generate_answer(question: str, context: str) -> str:
    """Generate an answer given the question and context."""
    prompt = QA_PROMPT.format(context=context[:4000], question=question)
    try:
        resp = requests.post(
            f"{BASE_URL}/chat/completions",
            headers=HEADERS,
            json={
                "model": JUDGE_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 100,
                "temperature": 0.0
            },
            timeout=30
        )
        if resp.status_code != 200:
            return ""
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        return ""

def judge_answer(question: str, gold_answer: str, generated_answer: str) -> dict:
    """Judge whether the generated answer is correct."""
    prompt = JUDGE_PROMPT.format(
        question=question,
        gold_answer=str(gold_answer),
        generated_answer=generated_answer
    )
    
    for attempt in range(3):
        try:
            resp = requests.post(
                f"{BASE_URL}/chat/completions",
                headers=HEADERS,
                json={
                    "model": JUDGE_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 150,
                    "temperature": 0.0
                },
                timeout=30
            )
            
            if resp.status_code == 429:
                wait = 2 ** attempt
                print(f"[judge] Rate limited, waiting {wait}s...", flush=True)
                time.sleep(wait)
                continue
            
            if resp.status_code != 200:
                return {"correct": False, "score": 0.0, "reason": f"API error {resp.status_code}"}
            
            text = resp.json()["choices"][0]["message"]["content"].strip()
            
            # Extract JSON from response
            try:
                result = json.loads(text)
                return result
            except json.JSONDecodeError:
                import re
                json_match = re.search(r'\{[^}]+\}', text)
                if json_match:
                    try:
                        return json.loads(json_match.group())
                    except Exception:
                        pass
                return {"correct": False, "score": 0.0, "reason": f"Parse error: {text[:100]}"}
        except Exception as e:
            if attempt == 2:
                return {"correct": False, "score": 0.0, "reason": str(e)}
            time.sleep(1)
    return {"correct": False, "score": 0.0, "reason": "Max retries exceeded"}

# Run generative QA + judge on ALL QA pairs
print("\n[run] Starting generative QA + judge scoring (FULL: all 1,986 QA pairs)...", flush=True)
print(f"[run] Mode: {'hybrid' if embedder else 'lexical'} + extraction + generative QA", flush=True)

scores = []
t0 = time.time()

for i, sample in enumerate(ds):
    conv_id, records, qa_pairs = convert_sample(sample)
    if not records or not qa_pairs:
        continue
    
    scopes = {r.scope for r in records}
    engine = ScopeEngine(known_scopes=scopes)
    sub = create_substrate(conv_id, records, engine=engine, embedder=embedder)
    ctx = make_ctx()
    
    # Defensive: if embedder was provided but vector index is incomplete,
    # fall back to lexical-only for this conversation.
    recall_embedder = embedder
    if recall_embedder is not None:
        try:
            incomplete = any(
                not sub.vector_index_complete(r.scope, ctx, model_id=recall_embedder.model_id)
                for r in records
            )
        except Exception:
            incomplete = True
        if incomplete:
            print(f"[run] Conv {conv_id}: vector index incomplete, falling back to lexical", flush=True)
            recall_embedder = None
    
    try:
        # Run extraction
        if recall_embedder is not None:
            try:
                claims = extract_facts(sub, ctx, "benchmark", limit=50)
                written = write_claims(sub, ctx, claims)
                if written > 0:
                    print(f"[run] Conv {conv_id}: extracted {written} claims", flush=True)
            except Exception as e:
                print(f"[run] Conv {conv_id}: extraction failed: {e}", flush=True)
        
        for qa in qa_pairs:
            # Get recall results
            result = run_recall(sub, qa.question, embedder=recall_embedder)
            context = "\n".join(result.selected_texts[:10])
            
            # Generate answer
            generated = generate_answer(qa.question, context)
            
            # Judge answer
            judge_result = judge_answer(qa.question, qa.gold_answer, generated)
            scores.append({
                "question_id": qa.question_id,
                "generated_answer": generated,
                "judge_correct": judge_result.get("correct", False),
                "judge_score": judge_result.get("score", 0.0),
                "judge_reason": judge_result.get("reason", ""),
            })
            
            if len(scores) % 100 == 0:
                elapsed = time.time() - t0
                correct = sum(1 for s in scores[:len(scores)] if s["judge_correct"])
                print(f"[run] Processed {len(scores)}/{1986} QA pairs ({elapsed:.1f}s, ~{len(scores)/elapsed:.1f}/s, accuracy={correct/len(scores):.1%})", flush=True)
    finally:
        sub.close()

elapsed = time.time() - t0
print(f"\n[run] Processed {len(scores)} QA pairs in {elapsed:.1f}s", flush=True)

# Aggregate
correct = sum(1 for s in scores if s["judge_correct"])
avg_score = sum(s["judge_score"] for s in scores) / len(scores) if scores else 0.0

results = {
    "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "judge_model": JUDGE_MODEL,
    "recall_mode": "hybrid" if embedder else "lexical",
    "eval_method": "generative_qa",
    "total_judged": len(scores),
    "correct": correct,
    "accuracy": correct / len(scores) if scores else 0.0,
    "avg_score": avg_score,
    "elapsed_s": elapsed,
    "samples": scores[:10]
}

(REPO / "benchmarks" / "judge-results.json").write_text(json.dumps(results, indent=2) + "\n")
print(json.dumps(results, indent=2), flush=True)
print("\n[Done] judge-results.json written", flush=True)
