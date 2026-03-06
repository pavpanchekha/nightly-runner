# Covered Bugs

| Date | Commit | Message | Test name |
|---|---|---|---|
| 2025-11-03 | `25ab170` | try/catch around report handling errors like broken links | `test_runner_handles_invalid_report_destination` |
| 2025-04-08 | `c2dbe41` | Fix report_dirs that are paths | `test_runner_publishes_nested_report_directory` |
| 2025-03-27 | `f197ffe` | Woops, got all the directories wrong | `mypy` |
| 2025-03-27 | `c88246f` | Fixes to report= | `mypy` |
| 2025-03-27 | `1004524` | Woops | `mypy` |
| 2025-02-17 | `cec292a` | Try to fix more submodule-related crashes? | `test_submodule_regression` |
| 2024-08-09 | `37605d2` | Fix (I hope) a bug with stale submodules | `test_submodule_regression` |
| 2024-08-05 | `7085a0f` | Update submodules on checkout (necessary, or permanently broken) | `test_submodule_regression` |
| 2024-08-05 | `4ec8241` | Fix dryrun again | `test_nr_dryrun_baseline` |
| 2024-08-02 | `6e20c84` | Fix outdated code | `test_adding_new_branch_gets_scheduled` |
| 2024-08-02 | `68569c5` | Woops | `mypy` |
| 2024-07-31 | `c18a7ca` | Bug | `test_nr_dryrun_vanilla` |
| 2024-07-31 | `bf27d55` | Fix list branches format | `test_commits_only_schedule_changed_branches` |
| 2024-07-31 | `8aa4618` | Woops | `mypy` |
| 2024-07-31 | `60d912a` | Woops, really bad! | `test_runner_tracks_remote_feature_branch_tip` |
| 2024-07-29 | `ac57e84` | Woops | `mypy` |
| 2024-07-29 | `4986a02` | Woops | `test_nightly_clears_transient_runner_state` |
| 2024-07-01 | `86276b3` | Fix log lock | `test_nightly_clears_transient_runner_state` |
| 2023-10-06 | `a3068a4` | Fix handling of secrets dir | `mypy` |
| 2023-10-06 | `1db92ad` | Woops, import type stuff | `mypy` |
| 2023-10-01 | `89cb65b` | Avoid hard crash in rare case | `test_runner_writes_metadata_for_successful_run` |
| 2023-03-26 | `5fefee2` | Fix handling of wait() | `test_nr_dryrun_vanilla` |
| 2023-03-15 | `6e4a166` | Fix more use of branch name vs dir | `test_deleting_branch_removes_worktree_directory` |
| 2023-01-26 | `85f0063` | Fix log indentation | `mypy` |
| 2023-01-26 | `1634b50` | Crash if token not actually specified | `mypy` |
| 2023-01-06 | `ad10abe` | Fix types | `mypy` |
| 2022-11-25 | `ae81946` | Ah, woops | `test_runner_writes_metadata_for_successful_run` |
| 2022-11-04 | `6158032` | Fix error handling and printing | `test_runner_handles_invalid_report_destination` |
| 2022-10-31 | `c7f980c` | Woops | `test_nr_dryrun_vanilla` |
| 2022-10-11 | `f067865` | Woops | `test_nr_dryrun_vanilla` |
| 2022-06-09 | `d409af2` | Fix timeout, check | `test_runner_timeout_returns_failure_and_writes_metadata` |
| 2022-06-09 | `6cc4764` | Woops | `test_nightly_clears_transient_runner_state` |
| 2022-06-09 | `5d12b3c` | Woops | `test_nightly_writes_log_under_configured_logs_dir` |
| 2022-06-09 | `4273cfa` | Oops | `test_clean_removes_unknown_files_but_keeps_branch_metadata` |
| 2022-06-09 | `2ba4d46` | Woops | `test_nightly_clears_transient_runner_state` |
| 2022-06-09 | `1dee753` | Woops | `test_clean_removes_unknown_files_but_keeps_branch_metadata` |
| 2022-06-09 | `0821fc3` | Bug | `test_nightly_clears_transient_runner_state` |
| 2022-06-09 | `043b6d9` | Fix baseline | `test_nr_dryrun_baseline` |
| 2022-06-07 | `9db25f1` | More bugs | `test_runner_publishes_reports` |
| 2022-06-07 | `8a2d86d` | Woops | `test_runner_publishes_reports` |
| 2022-06-06 | `c533115` | Bug fixes + dryrun feature | `test_nr_dryrun_vanilla` |
| 2022-06-06 | `41c0797` | Many, many bug fixes | `test_clean_removes_unknown_files_but_keeps_branch_metadata` |
| 2022-05-03 | `c4bf141` | Woops | `test_clean_removes_unknown_files_but_keeps_branch_metadata` |
| 2022-05-03 | `55a3de6` | Woops | `test_nightly_writes_log_under_configured_logs_dir` |
| 2022-05-03 | `3824dfc` | Fixed | `test_runner_timeout_returns_failure_and_writes_metadata` |
| 2022-05-03 | `1816b59` | Oops | `test_runner_timeout_returns_failure_and_writes_metadata` |
| 2022-05-02 | `1870f55` | Woops | `test_runner_timeout_returns_failure_and_writes_metadata` |
| 2021-08-25 | `8ee2bcd` | Woops | `test_runner_timeout_returns_failure_and_writes_metadata` |
| 2021-08-03 | `cd4b81c` | Fix baseurl | `test_load_normalizes_baseurl_with_trailing_slash` |
| 2021-08-03 | `2763cac` | Woops | `test_nightly_writes_log_under_configured_logs_dir` |
| 2021-03-11 | `411c269` | Check each function execution and don't crash | `test_submodule_regression` |

# Non-covered Bugs

## Needs Mocks (16)

| Date | Commit | Message | Mock needed |
|---|---|---|---|
| 2022-11-07 | `31d978a` | Woops | `apt` |
| 2022-11-07 | `625b562` | Fix dry_run logic again | `apt` |
| 2022-11-07 | `bd4351c` | Fix apt + dryrun handling | `apt` |
| 2020-12-08 | `e296c15` | Fix the slack output | `slack` |
| 2022-06-06 | `dd37d6d` | Fix slack URL | `slack` |
| 2022-06-09 | `677d705` | Fix lacking info | `slack` |
| 2022-06-10 | `1661a2e` | Fix log dir | `slack` |
| 2022-11-07 | `9b9767c` | More woops | `slack` |
| 2022-11-08 | `b750dae` | Woops | `slack` |
| 2022-11-10 | `7c6c235` | Woops | `slack` |
| 2022-11-10 | `d2886d0` | Woops | `slack` |
| 2024-08-05 | `5640cf7` | Post fatal errors from dry runs | `slack` |
| 2024-08-05 | `c1046ca` | Fix error post in dry run | `slack` |
| 2025-12-29 | `d238269` | Fix the slack channel | `slack` |
| 2024-04-26 | `6082c2c` | Woops | `slurm` |
| 2026-01-04 | `8fd6c58` | Fix queued job detection | `slurm` |

## Old / Obsolete (26)

| Date | Commit | Message | Obsolete feature |
|---|---|---|---|
| 2020-12-07 | `94a0a3b` | Bugs | `cli` |
| 2020-12-07 | `a5f0176` | Another bug | `cli` |
| 2020-12-07 | `de9c8b1` | More argument name bugs | `cli` |
| 2020-12-08 | `60099ae` | Dict bug | `cli` |
| 2020-12-08 | `7e47fe4` | Another bug | `cli` |
| 2020-12-08 | `cacb867` | Bug in NR | `cli` |
| 2020-12-09 | `2988515` | Woops | `cli` |
| 2020-12-09 | `f32637c` | More bugs | `cli` |
| 2020-12-09 | `f344ab3` | Fix URL error message | `cli` |
| 2020-12-11 | `f523b48` | Oops | `cli` |
| 2021-01-01 | `36000a2` | Woops | `cli` |
| 2021-01-07 | `3d931b2` | Woops | `cli` |
| 2021-01-28 | `48f1b51` | Woops | `cli` |
| 2021-05-06 | `5297fce` | Fix handling of image results | `cli` |
| 2021-07-24 | `020526a` | Fix multi-word nightly-results arguments | `cli` |
| 2021-07-25 | `64db465` | Woops | `cli` |
| 2021-07-26 | `b968ff9` | Woops | `cli` |
| 2021-07-28 | `8e89ee2` | More nightly fixes | `cli` |
| 2021-08-03 | `58e5588` | More fixes | `cli` |
| 2021-08-11 | `5640069` | Woops lol | `cli` |
| 2025-09-19 | `85b20fc` | Fix return code stuff | `sync-br` |
| 2022-11-26 | `65f7148` | Fix PATH using systemctl-run | `systemd` |
| 2022-11-26 | `ca69868` | Woops, run as root session manager | `systemd` |
| 2022-06-07 | `26da246` | Oops | `wait` |
| 2023-01-06 | `b22ad74` | Fixed many bugs | `wait` |
| 2023-05-05 | `b3c2cb2` | Fix bug | `wait` |

## Skipped (7)

| Date | Commit | Message | Skip reason |
|---|---|---|---|
| 2022-06-09 | `e6ad0c9` | Fix order of post vs run | `sysupdate` |
| 2022-06-09 | `e77f8d8` | Woops | `sysupdate` |
| 2022-10-12 | `401e032` | Catch fatal errors during update | `sysupdate` |
| 2022-10-12 | `b293444` | Fix pullconf / pullself | `sysupdate` |
| 2022-11-25 | `a4fb3bf` | Fix reload-on-pull | `sysupdate` |
| 2022-11-26 | `9324b4e` | Fix commit printing | `sysupdate` |
| 2023-10-06 | `5612606` | Add compat fix for shlex.join | `sysupdate` |
