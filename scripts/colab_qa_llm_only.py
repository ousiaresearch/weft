#!/usr/bin/env python3
"""Run LLM answer + judge on pre-computed contexts via TokenHarbor."""
import json, os, sys, time

BASE_URL = "https://tokenharbor.ai/v1"

# TokenHarbor
TOKENS = []
for k in ["TOKENHARBOR_API_KEY_1", "TOKENHARBOR_API_KEY_2"]:
    v = os.environ.get(k, "")
    if v:
        TOKENS.append(v)
if not TOKENS:
    try:
        TOKENS = [open(os.path.expanduser("~/.config/vireth/th_token")).read().strip()]
    except:
        pass
if not TOKENS:
    print("[ERROR] No tokens")
    sys.exit(1)

_token_idx = 0
_token_lock = __import__("threading").Lock()
def next_token():
    global _token_idx
    with _token_lock:
        t = TOKENS[_token_idx % len(TOKENS)]
        _token_idx += 1
        return t

JUDGE_MODEL = os.environ.get("TOKENHARBOR_JUDGE_MODEL", "gpt-6-luna")
ANSWER_MODEL = os.environ.get("TOKENHARBOR_ANSWER_MODEL", "gpt-6-luna")

import requests

print(f"[setup] Judge: {JUDGE_MODEL}, Answer: {ANSWER_MODEL}", flush=True)

# Verify
for m in [JUDGE_MODEL, ANSWER_MODEL]:
    r = requests.post(f"{BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {next_token()}", "Content-Type": "application/json"},
        json={"model": m, "messages": [{"role": "user", "content": "Say ok"}], "max_tokens": 5}, timeout=30)
    print(f"[setup] {m}: {r.status_code}", flush=True)
    if r.status_code != 200:
        sys.exit(1)

# Load pre-computed contexts
with open("/content/qa-contexts-v1.json") as f:
    data = json.load(f)
samples = data["samples"]
print(f"[run] Loaded {len(samples)} QA contexts", flush=True)

# Improved prompts
QA_PROMPT = """You are a precise research assistant. Answer the question using ONLY the provided context excerpts.

Rules:
- If the context contains the answer, provide it directly and concisely.
- If the context does not contain sufficient information, respond with exactly "I don't know."
- Do not use outside knowledge.
- Do not speculate or infer beyond what is explicitly stated.
- If multiple excerpts provide relevant information, synthesize them into one coherent answer.

Context:
{context}

Question: {question}

Answer:"""

JUDGE_PROMPT = """You are an expert evaluator assessing whether a generated answer correctly addresses a question based on the provided context.

Question: {question}
Gold Answer: {gold_answer}
Generated Answer: {generated_answer}

Context:
{context}

Evaluation Criteria:
1. Correctness: Does the generated answer match the gold answer given the context?
2. Evidence: Is the answer supported by the context?
3. Precision: Is the answer concise and directly responsive?

Scoring:
- 1.0: Fully correct, matches gold answer precisely or with acceptable paraphrase.
- 0.7: Partially correct - captures key information but missing details or has minor inaccuracies.
- 0.0: Incorrect, unsupported by context, or completely misses the question.

Respond with ONLY a JSON object: {{"correct": true/false, "score": float, "reason": "brief explanation"}}"""

def generate_answer(question, context):
    prompt = QA_PROMPT.format(context=context[:8000], question=question)
    try:
        r = requests.post(f"{BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {next_token()}", "Content-Type": "application/json"},
            json={"model": ANSWER_MODEL, "messages": [{"role": "user", "content": prompt}], "max_tokens": 150, "temperature": 0.0},
            timeout=30)
        if r.status_code != 200:
            return ""
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        return ""

def judge_answer(question, gold_answer, generated_answer, context):
    prompt = JUDGE_PROMPT.format(
        question=question,
        gold_answer=str(gold_answer),
        generated_answer=generated_answer,
        context=context[:8000]
    )
    for attempt in range(3):
        try:
            r = requests.post(f"{BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {next_token()}", "Content-Type": "application/json"},
                json={"model": JUDGE_MODEL, "messages": [{"role": "user", "content": prompt}], "max_tokens": 200, "temperature": 0.0},
                timeout=30)
            if r.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            if r.status_code != 200:
                return {"correct": False, "score": 0.0, "reason": f"API {r.status_code}"}
            text = r.json()["choices"][0]["message"]["content"].strip()
            try:
                return json.loads(text)
            except:
                import re
                m = re.search(r'\{[^}]+\}', text)
                if m:
                    try: return json.loads(m.group())
                    except: pass
                return {"correct": False, "score": 0.0, "reason": f"Parse: {text[:100]}"}
        except Exception as e:
            if attempt == 2:
                return {"correct": False, "score": 0.0, "reason": str(e)}
            time.sleep(1)
    return {"correct": False, "score": 0.0, "reason": "Max retries"}

print(f"[run] Processing {len(samples)} QA pairs with {ANSWER_MODEL} + judge {JUDGE_MODEL}", flush=True)
t0 = time.time()
scores = []
for s in samples:
    generated = generate_answer(s["question"], s["context"])
    judge_result = judge_answer(s["question"], s["gold_answer"], generated, s["context"])
    scores.append({
        "question_id": s["question_id"],
        "question": s["question"],
        "gold_answer": s["gold_answer"],
        "generated_answer": generated[:300],
        "judge_correct": judge_result.get("correct", False),
        "judge_score": judge_result.get("score", 0.0),
        "judge_reason": judge_result.get("reason", ""),
    })
    print(f"[{len(scores)}/{len(samples)}] {s['question_id']}: correct={judge_result.get('correct')} score={judge_result.get('score', 0):.2f}", flush=True)

elapsed = time.time() - t0
correct = sum(1 for s in scores if s["judge_correct"])
avg_score = sum(s["judge_score"] for s in scores) / len(scores) if scores else 0.0

results = {
    "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "judge_model": JUDGE_MODEL,
    "answer_model": ANSWER_MODEL,
    "recall_mode": "hybrid",
    "eval_method": "generative_qa",
    "total_judged": len(scores),
    "correct": correct,
    "accuracy": correct / len(scores) if scores else 0.0,
    "avg_score": avg_score,
    "elapsed_s": elapsed,
    "samples": scores,
}

with open("/content/qa-mini-strong-models.json", "w") as f:
    json.dump(results, f, indent=2)

print(json.dumps(results, indent=2), flush=True)
print("\n[Done] qa-mini-strong-models.json written", flush=True)
