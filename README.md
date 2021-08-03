Nightly Test Runner
===================

This script runs integration tests nightly.

## Usage

Start with a Github project, say `$ORG/$PROJ`. The nightly runner will:

+ Download each branch
+ Run `make nightly`

So, to use this nightly runner, you will need to add a `make` rule
called `nightly` to your project. It should probably:

+ Run your project on all its tests
+ Upload the results somewhere

You might also want to delete your obsolete branches, so they don't
take up time running nightlies you don't care about.

## Features

+ *Logs*: The log directory `log/` has global, per-repository, and
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
    slack = https://hooks.slack.com/services/xxx/yyy/zzz
    master = main

Each heading corresponds to one repository to test. Under each heading
are key-value pairs in INI format. The supported configuration options
are:

+ `url`: the URL of the git repository. This overrides the section
  name and the `github` key.
+ `github`: the Github repository name, in `user/repo` format. This
  overrides the section name.
+ `master`: change default branch, for example to `main`
+ `slack`: a webhook URL for posting to Slack after nightly runs
+ `baseline`: a branch to always run if any others are being run
+ `run`: When to run a branch. One of `baseline`, `always`, or `commit`

Once things are configured, run:

``` {.bash}
sudo systemctl link nightlies.service
sudo systemctl link nightlies.timer
sudo systemctl enable nightlies.timer
```

That will run the nightly daemon every night.
