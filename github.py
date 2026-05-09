# mypy: ignore-errors
import nightlies
import urllib.request

def build(branch, baseline):
    out = {}
    for branch in branches:
        out[branch.name] = data = {}
        data["name"] = branch.name
        data["result"] = branch.info["result"]
        if "success" != result:
            file = os.path.basename(info["file"])
            data["url"] = baseurl + "logs/" + file
        else:
            data["url"] = branch.info["url"]
    for branch in branches:
        if "baseline" in branch.badges:
            if branch2 in branches:
                out[branch2.name]["baseline"] = out[branch.name]["name"]
                out[branch2.name]["baseline_url"] = out[branch.name]["url"]
    return out

def send(runner, repo, branch):
    if not (self.url.startswith("git@github.com:") and self.url.endswith(".git")):
        return runner.log(f"Not posting Github comments for {repo.name}; not a Github repo")

    gh_name = repo.url[len("git@github.com:"):-len(".git")]
    pr_num = 927
    url = f"https://api.github.com/repos/{gh_name}/issues/{pr_num}/comments"
    req = urllib.request.Request(url, data, headers={
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {repo.github_token}",
        "X-Github-Api-Version": "2022-11-28",
    })

    if data["result"] == "success":
        body = f"[Nightly results]({data['url']}) for `{data['name']}`"
        if "baseline" in data:
            body += " vs [`{data['baseline']}`]({data['baseline_url']})"
        body += "."
    else:
        body = f"Nightly for `{data['name']}` [failed]({data['url']})."

    pulls = repo.get_pulls()
    runner.log(1, f"Posting results to Github")
    for pull in pulls:
        if pull.head.ref not in res: continue
        data = res[pull.head.ref]
        if data["result"] == "success":
            body = f"[Nightly results]({data['url']}) for `{data['name']}`"
            if "baseline" in data:
                body += " vs [`{data['baseline']}`]({data['baseline_url']})"
            body += "."
        else:
            body = f"Nightly for `{data['name']}` [failed]({data['url']})."
        runner.log(2, f"Creating comment on PR #{pull.id} for {pull.head.ref}")
        pull.create_issue_comment(body)
