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

+ *Logs*: The log files `$PROJ/out.log` and `$PROJ/error.log` are created
+ *Dedup*: A branch's nightly won't be run if no commit has happened
  since the last run.
+ *Heartbeat*: The file `last-run.txt` contains a timestamp for the
  last nightly run.

## Installing

Clone the repository somewhere and add the following line to your
crontab:

    0 1 * * * bash $DIR/nightlies/all.sh $GH1 $GH2 ...

(Replace `$DIR` with the directory cloned to and `$GH1` and up with
the Github org/project identifiers of projects to run nightlies for.)
