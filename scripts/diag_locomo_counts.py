"""Quick diagnostic: check LoCoMo conversation/QA distribution."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.full.locomo import _load_dataset, convert_sample

ds = _load_dataset()
print(f"Conversations: {len(ds)}")
total_qa = 0
for i, sample in enumerate(ds):
    _, _, qa_pairs = convert_sample(sample)
    print(f"  Conv {i}: {len(qa_pairs)} QA pairs")
    total_qa += len(qa_pairs)
print(f"Total QA: {total_qa}")
