#!/bin/bash

set -e -x
shopt -s nullglob

cd /data/pavpan/nightlies
PATH="$PATH:/home/p92/bin/"

get() {
	PROJ=$1
	BRANCH=$2
	mkdir -p $PROJ
	if [ ! -d $PROJ/$BRANCH ]; then
		git clone https://github.com/$GITHUB.git $PROJ/$BRANCH
	fi
	git -C $PROJ/$BRANCH fetch origin --prune
	git -C $PROJ/$BRANCH fetch origin $BRANCH
	git -C $PROJ/$BRANCH checkout $BRANCH
	git -C $PROJ/$BRANCH reset --hard origin/$BRANCH
}

branches() {
	PROJ=$1
	if [ ! -d $PROJ/master ]; then
		echo "Cannot find directory $PROJ/master" >&2
                return
	fi

	git -C $PROJ/master branch -r | grep -v 'master\|HEAD' | cut -d/ -f2 | tr '\n' ' '
}

check_branch() {
	PROJ=$1
	BRANCH=$2
        if [ -f "$PROJ/$BRANCH.last-commit" ]; then
            local LAST=$(cat "$PROJ/$BRANCH.last-commit")
            local CURRENT=$(git -C "$PROJ/$BRANCH" rev-parse origin/"$BRANCH")
            if [[ $LAST = $CURRENT ]]; then
                echo "Branch $BRANCH has not changed since last run; skipping" >&2
                return 1
            fi
        fi
	if ! make -C "$PROJ/$BRANCH" -n nightly >/dev/null 2>/dev/null ; then
		echo "Branch $BRANCH does not have nightly rule; skipping" >&2
		return 1
	fi
        return 0
}

filter_branches() {
    PROJ="$1"
    shift
    for branch in "$@"; do
        if check_branch "$PROJ" "$branch"; then
            printf "%s " "$branch"
        fi
    done
}

run() {
	PROJ=$1
	BRANCH=$2
	make -C "$PROJ/$BRANCH" nightly || echo "Running $PROJ on branch $BRANCH failed" >&2
        git -C "$PROJ/$BRANCH" rev-parse origin/"$BRANCH" > "$PROJ/$BRANCH.last-commit"
}

START=$(date +%s)

log() {
    printf "%s\t%s\n" $(( $(date +%s) - $START )) "$*" >> last.log
}

printf "\n\n" >> last.log
log "Nightly script starting up at $(date)"

for GITHUB in "$@"; do
    log "Beginning nightly run for $GITHUB"

    PROJ=$(echo "$GITHUB" | cut -d/ -f2)
    mkdir -p $PROJ

    # Redirect output to log file
    log "Redirecting output to $PROJ/out.log"
    exec >$PROJ/out.log 2>&1

    log "Downloading all $GITHUB branches"
    get "$PROJ" master
    branches="master `branches $PROJ`"
    for branch in $branches; do
    	get "$PROJ" $branch
    done

    log "Filtering $GITHUB branches $branches"
    branches=`filter_branches "$PROJ" $branches`

    log "Running $GITHUB branches $branches"
    for branch in $branches; do
    	log "Running tests on $GITHUB branch $branch"
    	run "$PROJ" $branch
    done

    log "Finished nightly run for $GITHUB"
done
