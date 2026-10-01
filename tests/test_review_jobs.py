from __future__ import annotations

import unittest

from src.review_jobs import ReviewJobStore


class ReviewJobStoreTests(unittest.TestCase):
    def test_job_reserves_items_and_records_success(self) -> None:
        jobs = ReviewJobStore()
        job = jobs.create_move_job([2, 1, 2], "Photos / 2026")
        self.assertEqual(job["item_ids"], [2, 1])
        self.assertEqual(jobs.reserved_item_ids(), [1, 2])

        jobs.run(str(job["id"]), lambda: {"saved_count": 2, "failed_count": 0})

        completed = jobs.get(str(job["id"]))
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["result"]["saved_count"], 2)
        self.assertEqual(jobs.reserved_item_ids(), [])

    def test_overlapping_jobs_are_rejected(self) -> None:
        jobs = ReviewJobStore()
        jobs.create_move_job([10, 11], "Documents")
        with self.assertRaises(ValueError):
            jobs.create_move_job([11, 12], "Documents")

    def test_worker_failure_is_reported_and_releases_items(self) -> None:
        jobs = ReviewJobStore()
        job = jobs.create_move_job([4], "Documents")

        def fail() -> dict:
            raise RuntimeError("disk unavailable")

        jobs.run(str(job["id"]), fail)
        failed = jobs.get(str(job["id"]))
        self.assertEqual(failed["status"], "failed")
        self.assertIn("disk unavailable", failed["error"])
        self.assertEqual(jobs.reserved_item_ids(), [])


if __name__ == "__main__":
    unittest.main()
