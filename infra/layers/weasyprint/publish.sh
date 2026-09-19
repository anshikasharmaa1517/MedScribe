#!/usr/bin/env bash
# Publish layer.zip to ap-south-1 and print the ARN to pass as WeasyPrintLayerArn.
set -euo pipefail
cd "$(dirname "$0")"
aws lambda publish-layer-version --layer-name medscribe-weasyprint --region ap-south-1 \
  --description "WeasyPrint + pango/cairo for python3.12 x86_64" \
  --compatible-runtimes python3.12 --compatible-architectures x86_64 \
  --zip-file fileb://layer.zip --query LayerVersionArn --output text
