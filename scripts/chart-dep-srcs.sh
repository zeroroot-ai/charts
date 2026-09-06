#!/usr/bin/env bash
# chart-dep-srcs.sh — print every source file a chart's vendored
# `charts/*.tgz` are built from (deploy#1600).
#
# WHY THIS EXISTS
#
# `helm/**/charts/*.tgz` is gitignored build output. `helm template` reads it
# silently: a stale, missing or wrong-branch tarball produces a WRONG render
# with no error, and every assertion made against that render is wrong too
# (deploy#1001, deploy#1465). The fix is to give the tarballs a real make
# dependency instead of a step someone has to remember — and a make dependency
# needs a prerequisite list.
#
# This script IS that list, and it is deliberately the ONLY place the list is
# computed. Both consumers read it:
#
#   Makefile                            $(shell scripts/chart-dep-srcs.sh …)
#                                       -> prerequisites of helm/<c>/.charts.stamp
#   scripts/check-chart-deps-fresh.py   -> the digest recorded in that stamp
#
# so the mtime rule make applies and the content digest the guard applies can
# never disagree about what "the sources" are.
#
# WHAT COUNTS AS A SOURCE
#
# `helm dependency update helm/<c>` PACKAGES each `file://` dependency from its
# working-tree directory. So <c>'s own charts/ is a function of exactly:
#
#   * <c>/Chart.yaml            — the dependency list itself
#   * every file under each file:// dependency's directory, recursively
#
# and NOT of <c>'s own templates/values: those are read at `helm template` time
# straight from the working tree, never from a tarball. Excluded from each
# dependency's tree:
#
#   charts/        its OWN build output — tracked by that chart's stamp, which
#                  the Makefile carries as a separate prerequisite. Including
#                  the tarballs here would make the digest depend on gzip
#                  timestamps and never settle.
#   Chart.lock     rewritten by `helm dependency update` itself — a
#                  prerequisite the recipe modifies never stops being newer
#                  than its target.
#   .charts.stamp  the stamp being computed.
#
# Everything else is in, `tests/` included: nothing in these charts is
# .helmignore'd, so a bats file really is packaged into the tarball. Erring
# towards a needless repackage is the safe direction; erring the other way is
# the silent-wrong-render defect this exists to kill.
#
# Usage:  scripts/chart-dep-srcs.sh helm/gibson-workloads
# Output: repo-root-relative paths, one per line, sorted and unique.
set -euo pipefail

usage() {
	echo "usage: $0 <chart-dir>   (e.g. $0 helm/gibson-workloads)" >&2
	exit 2
}

[ $# -eq 1 ] || usage
CHART_DIR=${1%/}
[ -f "$CHART_DIR/Chart.yaml" ] || {
	echo "$0: $CHART_DIR/Chart.yaml not found" >&2
	exit 1
}

# file:// dependency directories declared by a chart, repo-root-relative.
# The pattern matches the literal text Helm requires — `repository:
# "file://../gibson-common"` — with the quotes optional.
file_deps() {
	local d=$1 rel
	[ -f "$d/Chart.yaml" ] || return 0
	grep -oE 'repository:[[:space:]]*"?file://[^"[:space:]]+' "$d/Chart.yaml" 2>/dev/null |
		sed -E 's#^.*file://##' |
		while IFS= read -r rel; do
			[ -n "$rel" ] || continue
			if [ ! -d "$d/$rel" ]; then
				echo "$0: $d/Chart.yaml declares file://$rel but $d/$rel does not exist" >&2
				exit 1
			fi
			# Normalise ../ segments without resolving symlinks out of the repo.
			realpath --relative-to=. "$d/$rel"
		done
}

# Every source file under a chart directory, minus its own build output.
chart_files() {
	local d=$1
	find "$d" \
		-name .git -prune -o \
		-path "$d/charts" -prune -o \
		-type f ! -name Chart.lock ! -name .charts.stamp \
		-print
}

# A dependency's tree, plus the trees of ITS file:// dependencies. `_SEEN`
# breaks any cycle a hand-edited Chart.yaml could introduce.
_SEEN=""
emit_closure() {
	local d=$1 sub
	case " $_SEEN " in
	*" $d "*) return 0 ;;
	esac
	_SEEN="$_SEEN $d"
	chart_files "$d"
	while IFS= read -r sub; do
		[ -n "$sub" ] || continue
		emit_closure "$sub"
	done < <(file_deps "$d")
}

{
	echo "$CHART_DIR/Chart.yaml"
	while IFS= read -r dep; do
		[ -n "$dep" ] || continue
		emit_closure "$dep"
	done < <(file_deps "$CHART_DIR")
} | sort -u | {
	# A path with whitespace cannot be a make prerequisite: make would split it
	# into two names and silently drop the dependency — the exact class of
	# silent miss this whole mechanism exists to prevent. Fail loudly instead.
	bad=0
	while IFS= read -r p; do
		case $p in
		*[[:space:]]*)
			echo "$0: chart source path contains whitespace and cannot be a make prerequisite: $p" >&2
			bad=1
			;;
		esac
		printf '%s\n' "$p"
	done
	[ "$bad" -eq 0 ]
}
