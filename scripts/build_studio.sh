#!/usr/bin/env bash
# Build the MIT vue-fabric-editor as a same-origin sub-app served at /studio.
#
# Licensing notes:
# - Upstream code is MIT; the build output is a derived work, keep its LICENSE.
# - The upstream bundle ships commercial CJK fonts (华康金刚黑 / 汉体). They are
#   removed by this script and must not be redistributed; add only fonts you can
#   license for commercial use.
# - Template previews and sample images may have their own terms; verify before
#   publishing them in your own product.
set -euo pipefail

REPO=${VFE_REPO:-https://github.com/ikuaitu/vue-fabric-editor.git}
REF=${VFE_REF:-main}
WORK=${VFE_WORK:-/tmp/vue-fabric-editor-build}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
DEST="$ROOT/app/static/studio"

rm -rf "$WORK"
git clone --depth 1 --branch "$REF" "$REPO" "$WORK"
cd "$WORK"

if ! command -v corepack >/dev/null 2>&1; then
  echo "corepack 未安装；请先安装 Node 20+ 并启用 corepack" >&2
  exit 1
fi
corepack prepare pnpm@9.15.9 --activate >/dev/null
corepack pnpm install --no-frozen-lockfile
APP_BASE_PATH=/static/studio/ corepack pnpm build

rm -rf "$DEST"
mkdir -p "$DEST"
cp -R dist/. "$DEST"/
find "$DEST" -name '*.gz' -delete
# Remove bundled commercial fonts; never redistribute them.
find "$DEST" -type f \( -name '*华康*' -o -name '*汉体*' \) -delete
cp LICENSE "$DEST/LICENSE.vue-fabric-editor"

echo "editor built into $DEST"
echo "verify fonts and template assets before publishing; /studio will now serve it"
