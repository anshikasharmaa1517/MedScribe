"""
validator.py — clinical sanity checks on a resolved prescription.

Runs after resolver.py. Deterministic only: no LLM decides anything here.
Rules that matter:
  - unknown is NOT wrong. Missing indication data never raises a flag.
  - only CONTRADICTS (salt treats nothing related to the stated diagnosis)
    and known LASA pairs raise flags.
  - nothing is ever removed. Flags only.
  - no diagnosis yet -> PENDING_CHECK, not a flag.
"""
import csv
from collections import defaultdict

# Known look-alike / sound-alike salt pairs. Seed from ISMP list + Indian pairs.
# If two candidates for one spoken mention fall in the same pair, force CONFIRM
# regardless of score.
LASA_PAIRS = {
    frozenset({'Amlodipine', 'Amiloride'}),
    frozenset({'Metformin', 'Metronidazole'}),
    frozenset({'Tramadol', 'Trazodone'}),
    frozenset({'Clonazepam', 'Clonidine'}),
    frozenset({'Hydralazine', 'Hydroxyzine'}),
    frozenset({'Paracetamol', 'Acetazolamide'}),
    frozenset({'Prednisolone', 'Prednisone'}),
    frozenset({'Losartan', 'Lovastatin'}),
}


class Validator:
    def __init__(self, salts_csv='seed_salts.csv', conds_csv='seed_conditions.csv'):
        self.salts = {s['salt_id']: s for s in csv.DictReader(open(salts_csv))}
        self.conds = {c['condition_id']: c for c in csv.DictReader(open(conds_csv))}
        # synonym -> condition_ids (one synonym can map to several, e.g. "gas")
        self.syn = defaultdict(list)
        for c in self.conds.values():
            self.syn[c['name'].lower()].append(c['condition_id'])
            for s in c['synonyms'].split('|'):
                if s.strip():
                    self.syn[s.strip().lower()].append(c['condition_id'])

    def match_condition(self, diagnosis_text):
        """Free-text diagnosis -> condition_ids. Substring match is enough here."""
        if not diagnosis_text:
            return []
        d = diagnosis_text.lower()
        hits = []
        for syn, cids in self.syn.items():
            if syn in d:
                hits.extend(cids)
        return sorted(set(hits))

    def check_medicine(self, salt_ids, condition_ids):
        """Returns (verdict, reason). verdict: OK | UNKNOWN | CONTRADICTS"""
        if not condition_ids:
            return 'PENDING_CHECK', 'no diagnosis stated yet'
        if not salt_ids:
            return 'UNKNOWN', 'no salt mapping'

        expected_classes = set()
        for cid in condition_ids:
            expected_classes.update(
                x.strip() for x in self.conds[cid]['expected_salt_classes'].split('|'))

        for sid in salt_ids:
            salt = self.salts.get(sid)
            if not salt:
                continue
            # direct link: salt is listed against this condition
            linked = set(filter(None, salt['condition_ids'].split('|')))
            if linked & set(condition_ids):
                return 'OK', f"{salt['generic_name']} treats stated condition"
            # class-level link
            if salt['drug_class'] in expected_classes:
                return 'OK', f"{salt['drug_class']} is expected for this condition"

        # nothing matched. distinguish "we have data and it disagrees" from "no data"
        mapped = [self.salts[s] for s in salt_ids if s in self.salts]
        if any(s['condition_ids'].strip() for s in mapped):
            names = ', '.join(s['generic_name'] for s in mapped)
            classes = ', '.join(s['drug_class'] for s in mapped)
            return 'CONTRADICTS', f"{names} ({classes}) not indicated for stated diagnosis"
        return 'UNKNOWN', 'no indication data for this salt'

    def check_duplicates(self, medicines):
        """Same salt prescribed twice under different brands."""
        seen = defaultdict(list)
        for m in medicines:
            for sid in m.get('salt_ids', []):
                seen[sid].append(m.get('matched') or m.get('spoken'))
        return [(self.salts[sid]['generic_name'], brands)
                for sid, brands in seen.items()
                if len(brands) > 1 and sid in self.salts]

    def validate(self, medicines, diagnosis_text):
        cids = self.match_condition(diagnosis_text)
        out = []
        for m in medicines:
            verdict, reason = self.check_medicine(m.get('salt_ids', []), cids)
            status = m.get('status', 'AUTO')
            # validation can only downgrade, never upgrade
            if verdict == 'CONTRADICTS' and status == 'AUTO':
                status = 'CONFIRM'
            out.append({**m, 'status': status, 'clinical': verdict,
                        'clinical_reason': reason})
        return {
            'conditions_matched': [self.conds[c]['name'] for c in cids],
            'medicines': out,
            'duplicate_salts': self.check_duplicates(medicines),
        }


if __name__ == '__main__':
    from resolver import Resolver
    r, v = Resolver(), Validator()

    scenarios = [
        ("BP high hai, hypertension", ["amlong 5", "telma 40"]),
        ("hypertension", ["amifru 5"]),                      # wrong drug, should flag
        ("sugar diabetes", ["glycomet 500", "metrogyl 400"]),# metronidazole wrong here
        ("viral fever", ["dolo 650", "crocin 650"]),         # duplicate salt
        ("", ["dolo 650"]),                                  # no diagnosis yet
        ("loose motions", ["metrogyl 400", "econorm"]),      # metronidazole correct here
    ]

    for dx, spoken in scenarios:
        meds = [r.resolve(s) for s in spoken]
        res = v.validate(meds, dx)
        print(f"\nDx: {dx or '(none)'}  -> {res['conditions_matched'] or 'no match'}")
        for m in res['medicines']:
            print(f"   {m['status']:8} {m['clinical']:14} {str(m['matched']):22} {m['clinical_reason']}")
        for salt, brands in res['duplicate_salts']:
            print(f"   DUPLICATE: {salt} in {brands}")
