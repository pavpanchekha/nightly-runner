Nightlies
=========

This repository runs nightly integration tests for a set of repositories,
and it also publishes a small `nightlies` CLI for browsing the resulting logs
and reports on `nightly.cs.washington.edu`.

## CLI

The published CLI is meant for one-shot use with `uvx`:

```bash
uvx nightlies setup https://nightly.cs.washington.edu/
uvx nightlies --help
uvx nightlies list
uvx nightlies log main
uvx nightlies status main
uvx nightlies download main
```

Run these commands from a Git checkout for a nightly-enabled repository; the
CLI infers the repo name from the GitHub remote. If you are somewhere else,
use `-C` to point the CLI at the checkout:

```bash
uvx nightlies -C ~/src/herbie list
uvx nightlies -C ~/src/herbie log main
```

To install it persistently instead of using `uvx`:

```bash
uv tool install nightlies
```

Before you can use `list`, `log`, `status`, or `download`, run
`nightlies setup <url>`. The setup command prompts for a username and
password, then saves the nightly URL and credentials in an OS-appropriate
data directory so later commands can reuse them.

## Server

The rest of this repository is the nightly test runner and web server.

## Usage

Start with a Github project, say `$ORG/$PROJ`. The nightly runner will:

+ Download each branch with new commits
+ Run `make nightly`

So, to use this nightly runner, you will need to add a `make` rule
called `nightly` to your project. It should probably:

+ Run your project on all its tests
+ Upload the results somewhere

There's also Slack integration, so optionally your nightly run can post its results to your Slack channel.

## Features

+ *Logs*: The log directory `log/` has per-nightly and
  per-branch logs
+ *Dedup*: A branch's nightly won't be run if no commit has happened
  since the last run.
+ *Baselines*: The runner can be configured to run a nightly for the
  `master` branch if any other branch is being run.
+ *Slack support*: The runner can be configured to report each run to
  a Slack channel, and even to include a custom URL, image, or data.

## Installing

Clone the repository somewhere and create a file named
`nightlies.conf`. It should be formatted like this:

    [pavpanchekha/nightly-test]
    slack = uw/herbie
    master = main

Each heading corresponds to one repository to test. Under each heading
are key-value pairs in INI format. The supported configuration options
are:

+ `url`: the URL of the git repository. This overrides the section
  name and the `github` key.
+ `github`: the Github repository name, in `user/repo` format. This
  overrides the section name.
+ `master`: change default branch, for example to `main`.
+ `slack`: post after nightly runs; it's a Slack and channel name.
  The Slack is defined in the secrets file, the channel must be public.
+ `baseline`: a branch to always run if any others are being run
+ `run`: When to run a branch. One of `baseline`, `always`, or `commit`

Once things are configured, run:

``` {.bash}
sudo systemctl link nightlies.service
sudo systemctl link nightlies.timer
sudo systemctl enable nightlies.timer
```

That will run the nightly daemon every night.

Slack secrets are stored separately. For a `slack = uw/herbie` entry,
ensure the secrets file has a matching section like:

    [uw]
    token = xoxb-...

## Releasing The CLI

The PyPI package is also named `nightlies`, so users can run it directly with
`uvx nightlies`.

Build artifacts locally with:

```bash
uv build --no-sources
```

Publishing is handled by GitHub Actions via PyPI Trusted Publishing. Configure
PyPI to trust `.github/workflows/publish.yml`, then publish a GitHub release to
upload the new version to PyPI.
