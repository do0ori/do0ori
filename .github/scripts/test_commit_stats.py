import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import update_commit_stats as stats


class CommitStatsTests(unittest.TestCase):
    def test_seoul_boundaries_and_branch_counting(self):
        class Fixture:
            def query(self, query, variables):
                return {"user": {"id": "author"}}

            def pages(self, query, variables, path):
                if query in (stats.OWNED_QUERY, stats.CONTRIBUTED_QUERY):
                    return iter([{"name": "repo", "owner": {"login": "owner"}, "isFork": False}])
                if query == stats.BRANCH_QUERY:
                    return iter([{"name": "main"}, {"name": "feature"}])
                # UTC -> KST: 05:59, 06:00, 12:00, 18:00, 00:00 next day.
                hours = ("2026-10-12T20:59:00Z", "2026-10-12T21:00:00Z",
                         "2026-10-13T03:00:00Z", "2026-10-13T09:00:00Z",
                         "2026-10-13T15:00:00Z")
                commits = [{"oid": str(i), "committedDate": date} for i, date in enumerate(hours)]
                return iter(commits + [commits[0]])

        with patch.object(stats.time, "sleep"):
            self.assertEqual(stats.collect_counts(Fixture(), "user", ZoneInfo("Asia/Seoul")),
                             [2, 2, 2, 4])

    def test_pagination_and_partial_response_failure(self):
        api = stats.GitHub("fixture")
        responses = [
            {"items": {"nodes": [1], "pageInfo": {"hasNextPage": True, "endCursor": "next"}}},
            {"items": {"nodes": [2], "pageInfo": {"hasNextPage": False, "endCursor": None}}},
        ]
        with patch.object(api, "query", side_effect=responses) as query:
            self.assertEqual(list(api.pages("query", {}, ("items",))), [1, 2])
            self.assertEqual(query.call_args_list[1].args[1]["cursor"], "next")
        with patch.object(api, "query", return_value={"items": None}):
            with self.assertRaises(RuntimeError):
                list(api.pages("query", {}, ("items",)))

    def test_readme_preservation_and_empty_history(self):
        readme = "before\n" + stats.START + "old" + stats.END + "\nafter"
        section = stats.render_section([0, 0, 0, 0], datetime(2026, 10, 13, tzinfo=timezone.utc))
        updated = stats.replace_section(readme, section)
        self.assertTrue(updated.startswith("before\n" + stats.START))
        self.assertTrue(updated.endswith(stats.END + "\nafter"))
        self.assertEqual(updated.count("00.00 %"), 4)
        with self.assertRaises(ValueError):
            stats.replace_section("missing markers", section)

    def test_current_rendering_matches_previous_format(self):
        section = stats.render_section([1009, 5008, 5158, 2173],
                                       datetime(2026, 10, 13, 4, 8, 1, tzinfo=timezone.utc))
        self.assertIn("**I'm a Night 🦉**", section)
        self.assertIn("🌞 Morning                1009 commits        ⣿⣿", section)
        self.assertIn("07.56 %", section)
        self.assertIn("13/10/2026 04:08:01 UTC", section)


if __name__ == "__main__":
    unittest.main()
