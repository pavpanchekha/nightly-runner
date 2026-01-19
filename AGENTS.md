The nightlies are a platform to run research project evals any time
anyone commits to those projects. The results are served to the web
and also posted to Slack.

# Processes

The nightlies involve three running processes:

- server: HTTP entry point, starts nightlies, displays status.
- nightly: syncs with Github and starts runners, logs all actions.
- runner: runs single branch's nightly, saves log, posts results.

These processes must be loosely coupled:

- Server or cron starts nightly.
- Only one nightly process at a time.
- The nightly schedules runners via SLURM, which then runs them.
- Only one runner *per branch* at a time.

SLURM runs multiple runners concurrently if there are enough cores.

# State

Nightly state is stored in-disk in `running.pid`. It stores the PID,
what repo it is syncing, and a few other details.

Runner state is stored in SLURM. SLURM knows which jobs are running,
what branches they are running (in the job name), where their log file
is (in the job comment), and how long they've been running.

Repo state is stored on disk. Each repository has a `.checkout` folder
with a complete `git` checkout. Then each branch has a worktree, plus
a JSON file with extra state. The nightly process owns the checkout
while the runner process owns the worktree and JSON file.

The nightly process self-updates when run. The server shows a button
to restart when updated. This avoids manual deploy steps. Self-update
reuses the same `running.pid`.

# Coding Style

- Maintenance budget is low. No clever code, usually better to dumb
  down features.
- It's better to crash than to swallow failures. Asserts are good.
- The code has one user, its author, on one server.
- Target Python 3.14. Type-check with `mypy` before committing.
- Document all config file keys in `views/docs.view`.
- Document all architecture changes in this file
