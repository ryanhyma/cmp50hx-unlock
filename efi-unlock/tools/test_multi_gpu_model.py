"""Executable mocked-EFI checks for the coordinator's safety invariants.

This models EFI AllocatePages and the per-card coordinator boundary; it does
not claim to execute GPU firmware or replace hardware validation.
"""
import unittest


SUCCESS, OUT_OF_RESOURCES, DEVICE_ERROR, NOT_READY = range(4)


class Context:
    def __init__(self, bdf, limit=12):
        self.bdf, self.limit, self.allocations, self.retained = bdf, limit, [], 0
        self.bar, self.ss0, self.ss1 = 0, 0, 0

    def allocate(self, pages, fail=False):
        if len(self.allocations) == self.limit:
            return OUT_OF_RESOURCES
        if fail:
            return OUT_OF_RESOURCES
        self.allocations.append((self.bdf, pages))
        self.retained += pages * 4096
        return SUCCESS


class MockEfi:
    def __init__(self):
        self.chainloads = 0

    def chainload(self):
        self.chainloads += 1


def run(gpus, efi, all_mode=True):
    results, contexts = [], []
    for index, spec in enumerate(gpus):
        ctx = Context(spec["bdf"], spec.get("limit", 12))
        contexts.append(ctx)
        status = ctx.allocate(spec.get("pages", 1), spec.get("alloc_fail", False))
        if status == SUCCESS and spec.get("bar_fail"):
            status = DEVICE_ERROR
        if status == SUCCESS:
            ctx.bar = spec.get("bar", index + 1)
            ctx.ss0, ctx.ss1 = spec.get("ss0", 0x88888888), spec.get("ss1", 8)
            if (ctx.ss0, ctx.ss1) != (0x88888888, 8):
                status = DEVICE_ERROR
        results.append((status, ctx.ss0, ctx.ss1))
        if status != SUCCESS:
            results.extend([(NOT_READY, 0, 0)] * (len(gpus) - index - 1))
            break
    efi.chainload()
    return results, contexts


def select_mode(discovery=False, all_mode=False, bdf=None, count=None):
    if discovery and (all_mode or bdf is not None or count is not None):
        return None
    if all_mode and bdf is not None:
        return None
    if not all_mode and count is not None:
        return None
    if bdf is not None and len(bdf) != 7:
        return None
    return "discover" if discovery else "all" if all_mode else "bdf" if bdf else "first"


class MultiGpuModelTests(unittest.TestCase):
    def test_tracker_exhaustion_is_before_allocation(self):
        ctx = Context("02:00.0", limit=1)
        self.assertEqual(ctx.allocate(2), SUCCESS)
        self.assertEqual(ctx.allocate(2), OUT_OF_RESOURCES)
        self.assertEqual(len(ctx.allocations), 1)

    def test_failure_paths_fail_stop_and_chainload_once(self):
        efi = MockEfi()
        results, contexts = run([
            {"bdf": "02:00.0", "alloc_fail": True},
            {"bdf": "03:00.0"},
        ], efi)
        self.assertEqual([r[0] for r in results], [OUT_OF_RESOURCES, NOT_READY])
        self.assertEqual(efi.chainloads, 1)
        self.assertEqual(contexts[0].retained, 0)

    def test_bar_and_independent_ss_verification_fail(self):
        for bad in ({"bar_fail": True}, {"ss0": 0}, {"ss1": 0}):
            efi = MockEfi()
            results, _ = run([{"bdf": "02:00.0", **bad}], efi)
            self.assertEqual(results[0][0], DEVICE_ERROR)
            self.assertEqual(efi.chainloads, 1)

    def test_contexts_are_isolated_and_multiple_gpus_succeed(self):
        efi = MockEfi()
        results, contexts = run([
            {"bdf": "02:00.0", "pages": 3, "bar": 0xF0000000},
            {"bdf": "03:00.0", "pages": 4, "bar": 0xE0000000},
        ], efi)
        self.assertEqual([r[0] for r in results], [SUCCESS, SUCCESS])
        self.assertEqual(contexts[0].allocations[0][0], "02:00.0")
        self.assertEqual(contexts[1].allocations[0][0], "03:00.0")
        self.assertNotEqual(contexts[0].bar, contexts[1].bar)
        self.assertEqual(efi.chainloads, 1)

    def test_selection_guardrails_remain_in_the_c_source(self):
        with open(__file__.replace("test_multi_gpu_model.py", "../unlock50x_v1.c")) as source_file:
            source = source_file.read()
        self.assertIn("conflicting or malformed selection options", source)
        self.assertIn("if (!allMode && ok == targetCount)", source)

    def test_bdf_selection_and_conflicts(self):
        self.assertEqual(select_mode(), "first")
        self.assertEqual(select_mode(bdf="02:00.0"), "bdf")
        self.assertEqual(select_mode(discovery=True), "discover")
        self.assertEqual(select_mode(all_mode=True, count=8), "all")
        self.assertIsNone(select_mode(all_mode=True, bdf="02:00.0"))
        self.assertIsNone(select_mode(discovery=True, count=1))
        self.assertIsNone(select_mode(bdf="02:0.0"))


if __name__ == "__main__":
    unittest.main()
