#!/usr/bin/env bash
# Dry-run tests for volante-deploy against fake bundles (needs GNU coreutils, zip, unzip, rsync: runs on ubuntu).
#   deploy/pi/ci/test_volante_deploy.sh
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
VD=$HERE/volante-deploy
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
IN=$T/in; mkdir -p "$IN"
export VD_INPUT_ROOT=$IN VD_STAGING_ROOT=$T/stage VD_CONF=$T/none.conf VD_LOG=$T/log
fails=0

# build_bundle <name> <kit version> <with server: 1|0>  -> $IN/<name>.zip
build_bundle() {
  local d=$T/b/$1/oso-arena-pi
  mkdir -p "$d/kit" "$d/contract"
  echo wheel > "$d/kit/oso_arena-$2-py3-none-any.whl"; echo c > "$d/contract/CONTRACT.md"
  if [ "$3" = 1 ]; then mkdir -p "$d/server/deploy"; printf '#!/bin/sh\nexit 0\n' > "$d/server/deploy/install.sh"; fi
  (cd "$d" && find . -type f ! -name SHA256SUMS | sort | xargs sha256sum > ../sums.tmp && mv ../sums.tmp SHA256SUMS)
  (cd "$T/b/$1" && zip -qrX "$IN/$1.zip" oso-arena-pi)
}

# expect <name> <exit code> <output regex> <command...>
expect() {
  local name=$1 want=$2 re=$3 out rc=0; shift 3
  out=$("$@" 2>&1) || rc=$?
  if [ "$rc" = "$want" ] && grep -Eq -- "$re" <<<"$out"; then echo "ok   $name"
  else echo "FAIL $name (exit $rc, wanted $want, /$re/)"; printf '     | %s\n' "${out//$'\n'/$'\n     | '}"; fails=$((fails + 1)); fi
}

build_bundle good 0.4.0 1
build_bundle nosrv 0.4.0 0
build_bundle bump 0.5.0 1

expect "good bundle -> plan"          0 "dry-run: nothing installed" "$VD" --dry-run --installed-kit 0.4.0 arena "$IN/good.zip"
expect "kit bump refused"             3 "refusing kit 0.4.0 -> 0.5.0"     "$VD" --dry-run --installed-kit 0.4.0 arena "$IN/bump.zip"
expect "kit bump accepted with flag"  0 "kit bump 0.4.0 -> 0.5.0 allowed" "$VD" --dry-run --installed-kit 0.4.0 arena "$IN/bump.zip" --allow-kit-bump
expect "missing server/ -> skip"      0 "skipped: no server/ in bundle"   "$VD" --dry-run --installed-kit 0.4.0 arena "$IN/nosrv.zip"

# tampered file: change a byte inside the zip's payload
mkdir -p "$T/tamper" && cp -r "$T/b/good/oso-arena-pi" "$T/tamper/" && echo evil >> "$T/tamper/oso-arena-pi/contract/CONTRACT.md"
(cd "$T/tamper" && zip -qrX "$IN/tampered.zip" oso-arena-pi)
expect "tampered file -> checksum"    2 "checksum mismatch"               "$VD" --dry-run --installed-kit 0.4.0 arena "$IN/tampered.zip"
# extra file not covered by SHA256SUMS
echo x > "$T/tamper/oso-arena-pi/extra"; echo c > "$T/tamper/oso-arena-pi/contract/CONTRACT.md"
(cd "$T/tamper" && rm -f "$IN/extra.zip" && zip -qrX "$IN/extra.zip" oso-arena-pi)
expect "uncovered file -> refusal"    2 "does not cover"                  "$VD" --dry-run --installed-kit 0.4.0 arena "$IN/extra.zip"

# arguments and inputs
ln -s "$IN/good.zip" "$IN/link.zip"
mkdir -p "$T/out" && cp "$IN/good.zip" "$T/out/good.zip"
mkdir -p "$IN/sub" && ln -s "$IN/sub" "$IN/dirlink" && cp "$IN/good.zip" "$IN/sub/g.zip"
expect "symlinked input"              2 "not a regular file"              "$VD" --dry-run arena "$IN/link.zip"
expect "symlinked directory"          2 "symlinks or '..'"                "$VD" --dry-run arena "$IN/dirlink/g.zip"
expect "path outside the temp dir"    2 "must be under"                   "$VD" --dry-run arena "$T/out/good.zip"
expect "dotdot path"                  2 "symlinks or '..'"                "$VD" --dry-run arena "$IN/sub/../good.zip"
expect "relative path"                2 "absolute path"                   "$VD" --dry-run arena good.zip
expect "missing file"                 2 "not a regular file"              "$VD" --dry-run arena "$IN/nope.zip"
expect "no arguments"                 2 "usage"                           "$VD"
expect "unknown command"              2 "unknown argument: bogus"         "$VD" --dry-run bogus "$IN/good.zip"
expect "extra argument"               2 "unknown argument: --force"       "$VD" --dry-run arena "$IN/good.zip" --force
expect "kit flag on lan"              2 "unknown argument|unexpected"     "$VD" --dry-run lan "$IN/good.zip" --allow-kit-bump
expect "two inputs"                   2 "unknown argument"                "$VD" --dry-run arena "$IN/good.zip" "$IN/bump.zip"
expect "--installed-kit needs dry-run" 2 "only valid with --dry-run"      "$VD" --installed-kit 0.4.0 arena "$IN/good.zip"
expect "VD_ override without dry-run" 2 "only honoured with --dry-run"    "$VD" arena "$IN/good.zip"

# lan
mkdir -p "$T/lan/dist" "$T/lan/server" && echo h > "$T/lan/dist/index.html" && echo s > "$T/lan/server/server.mjs" && echo '{}' > "$T/lan/package.json"
tar -C "$T/lan" -cf "$IN/lan.tar" .
expect "lan tar -> plan"              0 "dry-run: nothing installed" "$VD" --dry-run lan "$IN/lan.tar"
rm "$T/lan/package.json"; tar -C "$T/lan" -cf "$IN/lan-bad.tar" .
expect "lan tar without package.json" 2 "tar must hold"                   "$VD" --dry-run lan "$IN/lan-bad.tar"
ln -s /etc/passwd "$T/lan/server/pw"; echo '{}' > "$T/lan/package.json"; tar -C "$T/lan" -cf "$IN/lan-link.tar" .
expect "lan tar with symlink"         2 "links or special"               "$VD" --dry-run lan "$IN/lan-link.tar"

# malicious archives: symlink / hardlink members are refused before extraction
mkdir -p "$T/evil/oso-arena-pi" && ln -s /etc "$T/evil/oso-arena-pi/l" && (cd "$T/evil" && zip -qry "$IN/evil.zip" oso-arena-pi)
expect "zip with symlink entry"       2 "links or special"                "$VD" --dry-run arena "$IN/evil.zip"
mkdir -p "$T/hl/dist" "$T/hl/server" && echo h > "$T/hl/dist/index.html" && ln "$T/hl/dist/index.html" "$T/hl/server/server.mjs" && echo '{}' > "$T/hl/package.json"
tar -C "$T/hl" -cf "$IN/lan-hl.tar" .
expect "lan tar with hardlink"        2 "links or special"                "$VD" --dry-run lan "$IN/lan-hl.tar"

if [ $fails -gt 0 ]; then echo "$fails test(s) failed"; exit 1; fi
echo "all volante-deploy tests passed"
