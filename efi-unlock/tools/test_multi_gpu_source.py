"""Non-hardware guardrails for the multi-GPU coordinator source."""
from pathlib import Path

source = Path(__file__).parents[1].joinpath("unlock50x_v1.c").read_text(encoding="utf-8")

for token in (
    "unlock_one_gpu(const CMP50_BDF *gpu",
    "CMP50_UNLOCK_CONTEXT",
    "CMP50_UNLOCK_RESULT",
    "cmp50_preflight_capacity",
    'u40x_has_load_option(ImageHandle, L"--all")',
    "parse_all_count",
    "result->ss0 == VAL_SS0_UNLOCKED",
    "result->ss1 == VAL_SS1_UNLOCKED",
    "fail-stop",
    "conflicting or malformed selection options",
    "return -1;",
):
    assert token in source, token

# The all-card coordinator must intentionally omit ReBAR from the worker.
worker = source[source.index("unlock_one_gpu("):source.index("EFI_STATUS\nefi_main")]
assert "#if 0 /* coordinator owns ReBAR policy and chainload */" in worker
print("multi-GPU source guardrails: OK")
