"""Adds phonetic_key to seed_brands.csv.
India-specific normalisation before Double Metaphone, so ASR variants
(Azee/Azi, Zerodol/Jerodol, Ciplox/Siplox) land in the same bucket.
"""
import csv, re
from metaphone import doublemetaphone

from core.settings import BRANDS_CSV

def normalise(name):
    n = name.lower()
    n = re.sub(r'ph', 'f', n)
    n = re.sub(r'^z', 'j', n)          # z/j confusion
    n = re.sub(r'v', 'w', n)           # v/w confusion
    n = re.sub(r'c(?=[ei])', 's', n)   # soft c
    n = re.sub(r'c', 'k', n)           # hard c
    n = re.sub(r'([aeiou])\1+', r'\1', n)  # collapse doubled vowels
    return n

rows = list(csv.DictReader(open(BRANDS_CSV)))
out_fields = list(rows[0].keys())
out_fields.insert(out_fields.index('aliases'), 'phonetic_key')

for r in rows:
    r['phonetic_key'] = doublemetaphone(normalise(r['base_brand']))[0]

with open(BRANDS_CSV, 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=out_fields)
    w.writeheader()
    w.writerows(rows)

# report collisions - these are your LASA candidates
from collections import defaultdict
buckets = defaultdict(list)
for r in rows:
    buckets[r['phonetic_key']].append(r['base_brand'])
print("phonetic collisions (different brands, same key):")
for k, v in sorted(buckets.items()):
    uniq = sorted(set(v))
    if len(uniq) > 1:
        print(f"  {k}: {uniq}")
