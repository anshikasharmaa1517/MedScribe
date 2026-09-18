"""Generates hotwords.json for Sarvam Saaras from the seed tables.
Run this whenever the seed CSVs change so ASR and matcher never drift apart.
"""
import csv, json

brands = list(csv.DictReader(open('seed_brands.csv')))
salts  = list(csv.DictReader(open('seed_salts.csv')))

words = []

# brand base names (highest value - these are what doctors say)
for b in brands:
    words.append(b['base_brand'])
    if b['modifier']:
        words.append(f"{b['base_brand']} {b['modifier']}")

# generic/salt names - doctors say these too, and pharmacists need them
for s in salts:
    for part in s['generic_name'].split('+'):
        words.append(part.strip())

# dosage shorthand spoken during consults
words += ["OD", "BD", "TDS", "QID", "SOS", "HS", "stat",
          "before food", "after food", "khaane se pehle", "khaane ke baad"]

# dedupe, keep order
seen, out = set(), []
for w in words:
    k = w.lower()
    if k not in seen:
        seen.add(k)
        out.append(w)

json.dump(out, open('hotwords.json', 'w'), indent=1, ensure_ascii=False)
print(f"{len(out)} hotwords written")
print("sample:", out[:8], "...", out[-6:])
