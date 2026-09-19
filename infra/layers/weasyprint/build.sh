#!/usr/bin/env bash
# Build a WeasyPrint Lambda layer (python3.12, x86_64) inside the Lambda base image so the
# native libs (pango, cairo, harfbuzz, fontconfig, fonts) match the runtime exactly.
# Output: layer.zip next to this script. Publish with publish.sh.
set -euo pipefail
cd "$(dirname "$0")"
docker run --rm --entrypoint bash -v "$(pwd -W 2>/dev/null || pwd):/out" public.ecr.aws/lambda/python:3.12 -c '
  set -e
  dnf install -y pango cairo harfbuzz fontconfig dejavu-sans-fonts zip >/dev/null 2>&1
  mkdir -p /layer/python /layer/lib /layer/fonts
  pip install -q --no-cache-dir weasyprint -t /layer/python
  # Collect every shared library the WeasyPrint stack dlopen()s, plus their dependencies.
  for lib in libpango-1.0 libpangocairo-1.0 libpangoft2-1.0 libcairo libgobject-2.0 libglib-2.0 \
             libgio-2.0 libgmodule-2.0 libharfbuzz libfontconfig libfreetype libfribidi libpixman-1 \
             libpng16 libthai libdatrie libxml2 libexpat libbrotlidec libbrotlicommon libgraphite2 \
             libXrender libX11 libXext libxcb libXau libpcre2-8 libffi libbz2 libcairo-gobject; do
    cp -L /usr/lib64/${lib}.so* /layer/lib/ 2>/dev/null || true
  done
  cp -r /usr/share/fonts/dejavu*/ /layer/fonts/ 2>/dev/null || cp -r /usr/share/fonts/* /layer/fonts/
  cat > /layer/fonts.conf <<FC
<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig><dir>/opt/fonts</dir><cachedir>/tmp/fontconfig</cachedir></fontconfig>
FC
  cd /layer && zip -qr /out/layer.zip . && du -sh /out/layer.zip
'
