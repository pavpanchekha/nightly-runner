#!/bin/bash

set -e
shopt -s nullglob

GITHUB="$1"
PROJ=$(echo "$GITHUB" | cut -d/ -f2)
shift

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
		echo "Cannot find directory $PROJ/master"
		exit 255
	fi

	git -C $PROJ/master branch -r | grep -v 'master\|HEAD' | cut -d/ -f2
}

run() {
	PROJ=$1
	BRANCH=$2
        if [ -f "$PROJ/$BRANCH.last-commit" ]; then
            local LAST=$(cat "$PROJ/$BRANCH.last-commit")
            local CURRENT=$(git -C "$PROJ/$BRANCH" rev-parse HEAD)
            if [[ $LAST = $CURRENT ]]; then
                echo "Branch $BRANCH has not changed since last run; skipping"
                return
            fi
            git -C "$PROJ/$BRANCH" rev-parse HEAD > "$PROJ/$BRANCH.last-commit"
        fi
	if ! make -C "$PROJ/$BRANCH" -n nightly 2>/dev/null ; then
		echo "Branch $BRANCH does not have nightly rule; skipping"
		return
	fi
	make -C "$PROJ/$BRANCH" nightly || echo "Running $PROJ on branch $BRANCH failed"
}

TIME=$(date +%s)

get "$PROJ" master
if [ $# -gt 0 ]; then
    cassius_branches="$@"
else
    cassius_branches="master `branches $PROJ`"
fi
for branch in $cassius_branches; do
	get "$PROJ" $branch
done

echo $TIME > ./last-run.txt

for branch in $cassius_branches; do
	echo "Running tests on branch" "$branch"
	run "$PROJ" $branch
done

rm -rf upload
mkdir upload
for branch in $cassius_branches; do
	echo "Saving results from branch" "$branch"
	[ -d "$PROJ/$branch/reports/" ] &&
	    cp -r "$PROJ/$branch/reports" "upload/$branch"
done
[ -f "$PROJ"/master/reports/reports.css ] && cp "$PROJ"/master/reports/report.css upload

echo "Uploading"
RPATH=/var/www/"$PROJ"/reports/$TIME/
rsync -r upload/ uwplse.org:$RPATH
ssh uwplse.org chmod a+x $RPATH
ssh uwplse.org chmod -R a+r $RPATH
echo "Uploaded to http://$PROJ.uwplse.org/reports/$TIME"

if [ -f "$PROJ"/master/infra/publish.sh ]; then
    ( cd "$PROJ"/master ; bash infra/publish.sh download index upload )
fi

rm -r upload
