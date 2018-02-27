#!/bin/bash

set -e
shopt -s nullglob

cd /data/pavpan/nightlies
PATH="$PATH:/home/p92/bin/"

for github in "$@"; do
	PROJ=$(echo "$github" | cut -d/ -f2)
	bash nightlies.sh $github > $PROJ/out.log 2> $PROJ/error.log &
done
