#!/bin/sh
# For developing roost: link ~/.local/bin/roost to this checkout so edits take effect
# immediately. Everyone else installs with install.sh (see README).
set -eu

src="$(cd "$(dirname "$0")" && pwd)/bin/roost"
dest="$HOME/.local/bin/roost"

mkdir -p "$(dirname "$dest")"
if [ -e "$dest" ] && [ ! -L "$dest" ]; then
  backup="$dest.before-link.$(date +%Y%m%d%H%M%S)"
  mv "$dest" "$backup"
  echo "Moved the existing roost to $backup"
fi
ln -sf "$src" "$dest"
echo "Linked $dest -> $src"

# roost's Claude Code plugin: Sessions roost starts load it from here
plugin_src="$(cd "$(dirname "$0")" && pwd)/plugin"
plugin_dest="$HOME/.roost/plugin"
mkdir -p "$(dirname "$plugin_dest")"
if [ -e "$plugin_dest" ] && [ ! -L "$plugin_dest" ]; then
  backup="$plugin_dest.before-link.$(date +%Y%m%d%H%M%S)"
  mv "$plugin_dest" "$backup"
  echo "Moved the existing plugin folder to $backup"
fi
ln -sfn "$plugin_src" "$plugin_dest"
echo "Linked $plugin_dest -> $plugin_src"

case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) echo "Add ~/.local/bin to your PATH to run roost from anywhere." ;;
esac
