#!/bin/sh
# Install or update roost from github.com/dmcphearson/agent-roost, with curl and python3 (both on
# every Mac). Homebrew users can run `brew install dmcphearson/tap/roost` instead.
#
#   curl -fsSL https://raw.githubusercontent.com/dmcphearson/agent-roost/main/install.sh -o /tmp/roost-install.sh && sh /tmp/roost-install.sh
#
# ROOST_REF=<tag or branch> installs a specific version instead of the latest release.
# The program files go in ~/.roost/app/<version>, with ~/.roost/app/current linking to it; the
# roost command, a small launcher, goes in ~/.local/bin.
set -eu

repo="dmcphearson/agent-roost"
dest="${ROOST_BIN:-$HOME/.local/bin}/roost"
app="$HOME/.roost/app"

fail() { echo "roost install: $*" >&2; exit 1; }

command -v curl >/dev/null 2>&1 || fail "needs curl."
command -v python3 >/dev/null 2>&1 || fail "needs python3."

if [ -L "$dest" ]; then
  fail "$dest is a link (a development checkout). Remove it first if you want the released version."
fi

ref="${ROOST_REF:-}"
if [ -z "$ref" ]; then
  ref="$(curl -fsSL --retry 3 -H "User-Agent: roost" "https://api.github.com/repos/$repo/releases/latest" \
    | python3 -c 'import json, sys; print(json.load(sys.stdin)["tag_name"])')" \
    || fail "couldn't read the latest release from github.com/$repo. Check your connection and try again."
fi

tmp="$(mktemp -d "${TMPDIR:-/tmp}/roost.XXXXXX")"
trap 'rm -rf "$tmp"' EXIT
curl -fsSL --retry 3 -H "User-Agent: roost" "https://github.com/$repo/archive/$ref.tar.gz" -o "$tmp/release.tar.gz" \
  || fail "couldn't download roost $ref from github.com/$repo."
mkdir "$tmp/x" "$tmp/new" "$tmp/new/bin"
tar -xzf "$tmp/release.tar.gz" -C "$tmp/x" || fail "the download isn't a release archive; nothing was installed."
top="$(find "$tmp/x" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
[ -f "$top/roost/__init__.py" ] && [ -f "$top/bin/roost" ] || fail "roost $ref has no roost/ package; nothing was installed."
mv "$top/roost" "$tmp/new/roost"
mv "$top/bin/roost" "$tmp/new/bin/roost"
chmod +x "$tmp/new/bin/roost"
version="$(python3 "$tmp/new/bin/roost" --version 2>/dev/null)" || fail "the downloaded roost didn't run; nothing was installed."
number="${version#roost }"

mkdir -p "$app" "$(dirname "$dest")"
rm -rf "${app:?}/$number"
mv "$tmp/new" "$app/$number"
ln -sfn "$number" "$app/current"
cp "$app/$number/bin/roost" "$dest.download"
mv -f "$dest.download" "$dest"
echo "Installed $version to $dest"
# the screens run on Textual, in ~/.roost/venv; roost setup does this too, so a failure here isn't fatal
python3 "$dest" setup textual || echo "roost install: Textual didn't install; 'roost setup' tries again."

case ":$PATH:" in
  *":$(dirname "$dest"):"*) ;;
  *) echo "Add $(dirname "$dest") to your PATH, e.g. echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc" ;;
esac
echo "Next: run 'roost setup' once. Later, 'roost update' gets new versions."
