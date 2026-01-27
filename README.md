Nightly Test Runner
===================

This script runs integration tests nightly.

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
