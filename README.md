Nightly Test Runner
===================

This script runs integration tests nightly.

## Usage

Start with a Github project, say `$ORG/$PROJ`. The nightly runner will:

+ Download each branch
+ Run `make nightly`, expecting results in `reports/`
+ Uploads the results to a timestamped directory in
  `/var/www/$PROJ/reports` on `uwplse.org`

So, to use this nightly runner, you will need to:

+ Create `/var/www/$PROJ/reports` on `uwplse.org`
+ Add a `make` rule called `nightly` to your project

You might also want to delete your obsolete branches, so they don't
take up time running nightlies you don't care about.

## Features

+ *Logs*: The log files `$PROJ/out.log` and `$PROJ/error.log` are created
+ *Dedup*: If the file `$PROJ/$BRANCH.last-commit` exists, that
  branch's nightly won't be rerun if no commit has happened since the
  last run.
+ *Heartbeat*: The file `last-run.txt` contains a timestamp for the
  last nightly run.

## Installing

Clone the repository somewhere and add the following line to your
crontab:

    0 1 * * * bash $DIR/nightlies/all.sh $GH1 $GH2 ...

(Replace `$DIR` with the directory cloned to and `$GH1` and up with
the Github org/project identifiers of projects to run nightlies for.)
