"""GitHub-only profile statistics, preserving the previous all-branch count.

Non-fork owned/collaborator and contributed repositories, all history, author
ID filtering, committedDate. Shared commits count once per branch as before.
The existing GH_TOKEN determines access to private repositories.
"""
import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

START = "<!--START_SECTION:waka-->"
END = "<!--END_SECTION:waka-->"
PAGE = "pageInfo { endCursor hasNextPage }"
REPOS = "nodes { name owner { login } isFork } " + PAGE
USER_QUERY = "query($login:String!) { user(login:$login) { id } }"
OWNED_QUERY = """query($login:String!, $cursor:String) {
 user(login:$login) {
  repositories(first:100, after:$cursor,
   orderBy:{field:CREATED_AT,direction:DESC},
   affiliations:[OWNER,COLLABORATOR], isFork:false) { %s }
 }
}""" % REPOS
CONTRIBUTED_QUERY = """query($login:String!, $cursor:String) {
 user(login:$login) {
  repositoriesContributedTo(first:100, after:$cursor,
   orderBy:{field:CREATED_AT,direction:DESC},
   includeUserRepositories:true) { %s }
 }
}""" % REPOS
BRANCH_QUERY = """query($owner:String!, $name:String!, $cursor:String) {
 repository(owner:$owner, name:$name) {
  refs(refPrefix:"refs/heads/", first:100, after:$cursor,
   orderBy:{field:TAG_COMMIT_DATE,direction:DESC}) {
   nodes { name } %s
  }
 }
}""" % PAGE
COMMIT_QUERY = """query($owner:String!, $name:String!, $branch:String!,
                       $author:ID!, $cursor:String) {
 repository(owner:$owner, name:$name) {
  ref(qualifiedName:$branch) { target { ... on Commit {
   history(first:100, after:$cursor, author:{id:$author}) {
    nodes { oid committedDate } %s
   }
  } } }
 }
}""" % PAGE


class GitHub:
    def __init__(self, token):
        if not token:
            raise ValueError("GH_TOKEN is required for cross-repository statistics")
        self.token = token

    def query(self, query, variables):
        request = Request(
            "https://api.github.com/graphql",
            data=json.dumps({"query": query, "variables": variables}).encode(),
            headers={"Authorization": f"Bearer {self.token}",
                     "Content-Type": "application/json",
                     "User-Agent": "profile-commit-stats"},
        )
        for attempt in range(4):
            try:
                with urlopen(request, timeout=60) as response:
                    result = json.load(response)
                if result.get("errors"):
                    # Avoid publishing partial statistics or private repo details.
                    raise RuntimeError("GitHub GraphQL returned errors; README left unchanged")
                if not result.get("data"):
                    raise RuntimeError("GitHub returned no data")
                return result["data"]
            except HTTPError as error:
                if error.code not in (429, 500, 502, 503, 504) or attempt == 3:
                    raise RuntimeError(f"GitHub request failed (HTTP {error.code})") from None
            except (URLError, TimeoutError):
                if attempt == 3:
                    raise RuntimeError("GitHub request failed after retries") from None
            time.sleep(2 ** attempt)

    def pages(self, query, variables, path):
        cursor = None
        while True:
            data = self.query(query, {**variables, "cursor": cursor})
            for key in path:
                if not isinstance(data, dict) or data.get(key) is None:
                    raise RuntimeError("GitHub collection unavailable; README left unchanged")
                data = data[key]
            yield from (node for node in data["nodes"] if node is not None)
            info = data["pageInfo"]
            if not info["hasNextPage"]:
                return
            next_cursor = info["endCursor"]
            if not next_cursor or next_cursor == cursor:
                raise RuntimeError("GitHub pagination did not advance")
            cursor = next_cursor


def collect_counts(api, login, local_timezone):
    user = api.query(USER_QUERY, {"login": login})["user"]
    if user is None:
        raise RuntimeError("Profile user was not found")
    repositories = {}
    for query, field in ((OWNED_QUERY, "repositories"),
                         (CONTRIBUTED_QUERY, "repositoriesContributedTo")):
        for repo in api.pages(query, {"login": login}, ("user", field)):
            if not repo["isFork"]:
                repositories[(repo["owner"]["login"], repo["name"])] = repo
    counts = [0, 0, 0, 0]  # Midnight, morning, daytime, evening.
    for index, (owner, name) in enumerate(repositories, 1):
        variables = {"owner": owner, "name": name}
        for branch in api.pages(BRANCH_QUERY, variables, ("repository", "refs")):
            commits = api.pages(
                COMMIT_QUERY,
                {**variables, "branch": "refs/heads/" + branch["name"],
                 "author": user["id"]},
                ("repository", "ref", "target", "history"),
            )
            seen = set()
            for commit in commits:
                if commit["oid"] in seen:
                    continue
                seen.add(commit["oid"])
                date = datetime.fromisoformat(commit["committedDate"].replace("Z", "+00:00"))
                counts[date.astimezone(local_timezone).hour // 6] += 1
            time.sleep(0.4)
        print(f"Processed repository {index}/{len(repositories)}", flush=True)
    return counts[1:] + counts[:1]


def render_section(counts, now):
    total = sum(counts)
    title = "I'm an Early 🐤" if sum(counts[:2]) >= sum(counts[2:]) else "I'm a Night 🦉"
    labels = ("🌞 Morning", "🌆 Daytime", "🌃 Evening", "🌙 Night")
    rows = []
    for label, count in zip(labels, counts):
        percent = round(count / total * 100, 2) if total else 0
        filled = round(percent / 4)
        bar = "⣿" * filled + "⣀" * (25 - filled)
        rows.append(f"{label:<25}{str(count) + ' commits':<20}{bar}   {percent:05.2f} % ")
    return (f"\n**{title}** \n\n```text\n" + "\n".join(rows)
            + "\n```\n\n\n\n Last Updated on "
            + now.astimezone(timezone.utc).strftime("%d/%m/%Y %H:%M:%S UTC") + "\n")


def replace_section(readme, section):
    if readme.count(START) != 1 or readme.count(END) != 1:
        raise ValueError("README must contain exactly one pair of section markers")
    start = readme.index(START) + len(START)
    end = readme.index(END)
    if end < start:
        raise ValueError("README section markers are reversed")
    return readme[:start] + section + readme[end:]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print the section without writing README")
    args = parser.parse_args()
    readme_path = Path(__file__).resolve().parents[2] / "README.md"
    readme = readme_path.read_text(encoding="utf-8")
    replace_section(readme, "")
    local_timezone = ZoneInfo(os.environ.get("PROFILE_TIMEZONE", "Asia/Seoul"))
    counts = collect_counts(GitHub(os.environ.get("GH_TOKEN")),
                            os.environ.get("PROFILE_USER", "do0ori"), local_timezone)
    section = render_section(counts, datetime.now(timezone.utc))
    if args.dry_run:
        print(section)
    else:
        readme_path.write_text(replace_section(readme, section), encoding="utf-8")


if __name__ == "__main__":
    main()
