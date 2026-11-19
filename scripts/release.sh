#!/usr/bin/env bash
# Release Lumi Crew: scripts/release.sh 0.2.0
#  1. checks: clean tree on main, CHANGELOG has a section for the version, tests pass
#  2. sets the version in pyproject.toml, commits, tags vX.Y.Z, pushes
#  3. creates the GitHub release (notes = CHANGELOG section)
#  4. updates Formula/lumi-crew.rb in OhneKommentar/homebrew-tap
set -euo pipefail

VERSION="${1:?usage: scripts/release.sh X.Y.Z}"
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "version must look like 1.2.3"; exit 1; }
REPO="OhneKommentar/lumi-crew"
TAP="OhneKommentar/homebrew-tap"
TAG="v$VERSION"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# ------------------------------------------------------------------ checks
[[ "$(git branch --show-current)" == "main" ]] || { echo "release from main only"; exit 1; }
[[ -z "$(git status --porcelain)" ]] || { echo "working tree not clean"; exit 1; }
git fetch -q origin
[[ "$(git rev-parse HEAD)" == "$(git rev-parse origin/main)" ]] || { echo "main is not in sync with origin/main"; exit 1; }
git rev-parse -q --verify "refs/tags/$TAG" >/dev/null && { echo "tag $TAG already exists"; exit 1; }
grep -q "^## \[$VERSION\]" CHANGELOG.md || { echo "CHANGELOG.md has no section '## [$VERSION]'"; exit 1; }

PY="${PYTHON:-.venv/bin/python}"
echo "▶ tests"
"$PY" -m pytest -q

# ------------------------------------------------------------------ version + tag
echo "▶ version $VERSION"
sed -i.bak -E "s/^version = \"[^\"]+\"/version = \"$VERSION\"/" pyproject.toml && rm pyproject.toml.bak
git add pyproject.toml
git diff --cached --quiet || git commit -q -m "Release $TAG"
git tag -a "$TAG" -m "Lumi Crew $TAG"
git push -q origin main "$TAG"

# ------------------------------------------------------------------ GitHub release
NOTES="$(awk -v v="$VERSION" '$0 ~ "^## \\[" v "\\]" {on=1; next} on && /^## \[/ {exit} on {print}' CHANGELOG.md)"
gh release create "$TAG" --repo "$REPO" --title "Lumi Crew $TAG" --notes "$NOTES"

# ------------------------------------------------------------------ Homebrew tap
TARBALL="https://github.com/$REPO/archive/refs/tags/$TAG.tar.gz"
SHA="$(curl -fsSL "$TARBALL" | shasum -a 256 | cut -d' ' -f1)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
gh repo clone "$TAP" "$WORK/tap" -- -q
mkdir -p "$WORK/tap/Formula"
sed -e "s/@VERSION@/$VERSION/g" -e "s/@SHA256@/$SHA/g" packaging/lumi-crew.rb.tmpl > "$WORK/tap/Formula/lumi-crew.rb"
git -C "$WORK/tap" add Formula/lumi-crew.rb
git -C "$WORK/tap" -c user.name="$(git config user.name)" -c user.email="$(git config user.email)" \
    commit -q -m "lumi-crew $VERSION"
git -C "$WORK/tap" push -q

echo "✔ released $TAG: brew update && brew upgrade lumi-crew"
