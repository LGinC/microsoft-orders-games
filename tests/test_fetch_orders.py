import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import fetch_orders as fo
import store


class FetchHelpersTest(unittest.TestCase):
    def test_is_stale_context(self):
        self.assertTrue(fo.is_stale_context("This action requires the context returned by the latest cooperate sync"))
        self.assertTrue(fo.is_stale_context("The action's observed content changed; synchronize and replan"))
        self.assertTrue(fo.is_stale_context("cooperation_sync_required"))
        self.assertFalse(fo.is_stale_context("ok"))
        self.assertFalse(fo.is_stale_context(None))

    def test_extract_csrf_nested(self):
        payload = {"result": {"value": {"csrf": "abc:def", "href": "https://x"}}}
        self.assertEqual(fo.extract_csrf(payload), "abc:def")
        self.assertEqual(fo.extract_csrf({"csrf": ""}), "")

    def test_extract_page(self):
        inner = {"continuationToken": "M10", "orders": [{"orderId": "a"}]}
        self.assertEqual(fo.extract_page({"result": inner})["orders"][0]["orderId"], "a")
        self.assertIsNone(fo.extract_page({"ok": True}))

    def test_unwrap_eval_prefers_fetch_payload(self):
        data = {"result": {"ok": True, "status": 200, "orders": []}}
        out = fo.unwrap_eval(data)
        self.assertTrue(out["ok"])
        self.assertEqual(out["status"], 200)

    def test_should_stop(self):
        known = {"a", "b"}
        self.assertEqual(
            fo.should_stop("incremental", known, {"overlap": True, "n": 10, "token": "x"}),
            "incremental",
        )
        self.assertIsNone(
            fo.should_stop("full", known, {"overlap": True, "n": 10, "token": "x"}),
        )
        self.assertEqual(
            fo.should_stop("full", known, {"overlap": False, "n": 0, "token": None}),
            "complete",
        )
        self.assertIsNone(
            fo.should_stop("incremental", set(), {"overlap": True, "n": 10, "token": "x"}),
        )

    def test_batch_js_substitution(self):
        js = (
            fo.FETCH_BATCH_JS.replace("__CONTINUATION__", json.dumps("M10L0R0D0"))
            .replace("__CSRF__", json.dumps("tok"))
            .replace("__MAX__", "4")
        )
        self.assertNotIn("__CONTINUATION__", js)
        self.assertIn('"M10L0R0D0"', js)
        self.assertIn('"tok"', js)

    def test_ingest_overlap(self):
        tmp = Path(tempfile.mkdtemp())
        store.RAW_DIR = tmp
        store.FETCH_LOG_PATH = tmp / "fetch_log.jsonl"
        store.ensure_dirs()
        existing = {"id-1": {"orderId": "id-1", "localTitle": "old"}}
        meta: dict = {}
        page = {
            "continuationToken": "T2",
            "orders": [{"orderId": "id-1", "localTitle": "old"}],
        }
        stats = fo.ingest_page(existing, meta, page, source="test", known_before={"id-1"})
        self.assertTrue(stats["overlap"])
        self.assertEqual(stats["n"], 1)
        self.assertEqual(stats["new"], 0)
        page2 = {
            "continuationToken": "T3",
            "orders": [{"orderId": "id-2", "localTitle": "new"}],
        }
        stats2 = fo.ingest_page(existing, meta, page2, source="test", known_before={"id-1"})
        self.assertFalse(stats2["overlap"])
        self.assertEqual(stats2["new"], 1)
        self.assertIn("id-2", existing)


if __name__ == "__main__":
    unittest.main()
