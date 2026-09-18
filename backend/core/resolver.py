"""
resolver.py — turns a spoken medicine mention into a resolved brand + status.

Status contract:
  AUTO    high score, clear margin           -> fill silently, green
  CONFIRM low margin / phonetic collision    -> amber, one tap to accept
  RESOLVE nothing credible                   -> red, blocks approval

The LLM never picks the medicine. It hands us spoken_name; this picks the id.
"""
import csv, re, json
from rapidfuzz import fuzz, process
from metaphone import doublemetaphone

from core.settings import BRANDS_CSV

# ---------------------------------------------------------------- thresholds
AUTO_SCORE   = 88   # top candidate must beat this to auto-fill
MIN_SCORE    = 62   # below this we don't guess at all
MIN_MARGIN   = 6    # top must beat 2nd by this much, else CONFIRM

NUM_WORDS = {
    'zero':'0','one':'1','two':'2','three':'3','four':'4','five':'5',
    'six':'6','seven':'7','eight':'8','nine':'9','ten':'10',
    'twenty':'20','twenty five':'25','forty':'40','fifty':'50',
    'sixty':'60','seventy five':'75','hundred':'100','two fifty':'250',
    'five hundred':'500','six fifty':'650','six twenty five':'625',
    'thousand':'1000','ek':'1','do':'2','teen':'3',
}
MODIFIERS = {'sp','ds','xt','cv','mr','od','sr','lb','dt','d','p','h','m',
             'plus','forte','duo','advance','retard','xl','xr','la','gp1',
             'dsr','rd','met','hfa','o','ls','cold','spas','qps','sl','lc'}


def normalise(name: str) -> str:
    n = name.lower()
    n = re.sub(r'ph', 'f', n)
    n = re.sub(r'^z', 'j', n)
    n = re.sub(r'v', 'w', n)
    n = re.sub(r'c(?=[ei])', 's', n)
    n = re.sub(r'c', 'k', n)
    n = re.sub(r'([aeiou])\1+', r'\1', n)
    return n


def parse_spoken(text: str) -> dict:
    """'dolo six fifty' -> {base:'dolo', modifier:None, strength:650}"""
    t = text.lower().strip()
    t = re.sub(r'\b(tab|tablet|cap|capsule|syrup|syp|inj|injection)\b', ' ', t)

    # spoken numbers -> digits (longest phrases first)
    for word in sorted(NUM_WORDS, key=len, reverse=True):
        t = re.sub(rf'\b{word}\b', NUM_WORDS[word], t)

    t = re.sub(r'[-,]', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip()

    strength = None
    m = re.search(r'(\d+(?:\.\d+)?)\s*(mg|mcg|g|ml|%)?', t)
    if m:
        strength = float(m.group(1))
        t = (t[:m.start()] + ' ' + t[m.end():]).strip()

    tokens = [tok for tok in t.split() if tok]
    modifier = None
    if tokens and tokens[-1] in MODIFIERS:
        modifier = tokens.pop()

    return {'base': ' '.join(tokens), 'modifier': modifier, 'strength': strength}


class Resolver:
    def __init__(self, brands_csv=BRANDS_CSV):
        self.brands = list(csv.DictReader(open(brands_csv)))
        self.buckets = {}
        self.lookup = {}          # exact alias -> row
        for b in self.brands:
            self.buckets.setdefault(b['phonetic_key'], []).append(b)
            self.lookup[b['base_brand'].lower()] = b
            for a in b['aliases'].split('|'):
                if a.strip():
                    self.lookup[a.strip().lower()] = b

    def _score(self, parsed, row):
        s = fuzz.ratio(normalise(parsed['base']), normalise(row['base_brand']))
        # modifier agreement is a hard signal: Pan vs Pan-D are different drugs
        pm = (parsed['modifier'] or '').lower()
        rm = (row['modifier'] or '').lower()
        if pm == rm:
            s += 6
        elif pm and rm and pm != rm:
            s -= 25
        elif pm != rm:
            s -= 10
        if parsed['strength'] and row['strength']:
            try:
                if abs(float(row['strength']) - parsed['strength']) < 0.01:
                    s += 8
            except ValueError:
                pass
        return min(s, 100)

    def resolve(self, spoken: str) -> dict:
        parsed = parse_spoken(spoken)

        # 1. exact alias hit
        hit = self.lookup.get(spoken.lower().strip())
        if hit:
            return self._result(spoken, parsed, [(100, hit)], 'AUTO', 'exact alias')

        # 2. candidates: phonetic bucket first, fall back to everything
        key = doublemetaphone(normalise(parsed['base']))[0]
        pool = self.buckets.get(key) or self.brands

        scored = sorted(((self._score(parsed, r), r) for r in pool),
                        key=lambda x: -x[0])[:5]
        if not scored or scored[0][0] < MIN_SCORE:
            return self._result(spoken, parsed, scored, 'RESOLVE', 'no credible match')

        top = scored[0][0]
        second = scored[1][0] if len(scored) > 1 else 0
        margin = top - second

        if top >= AUTO_SCORE and margin >= MIN_MARGIN:
            return self._result(spoken, parsed, scored, 'AUTO', f'score {top}, margin {margin}')
        if margin < MIN_MARGIN:
            return self._result(spoken, parsed, scored, 'CONFIRM',
                                f'only {margin} pts from {scored[1][1]["base_brand"]}')
        return self._result(spoken, parsed, scored, 'CONFIRM', f'score {top} below auto threshold')

    def _result(self, spoken, parsed, scored, status, reason):
        best = scored[0][1] if scored else None
        return {
            'spoken': spoken,
            'parsed': parsed,
            'status': status,
            'reason': reason,
            'brand_id': best['brand_id'] if best and status != 'RESOLVE' else None,
            'matched': (f"{best['base_brand']} {best['modifier']} {best['strength']}{best['unit']}"
                        .replace('  ', ' ').strip()) if best and status != 'RESOLVE' else None,
            'salt_ids': best['salt_ids'].split('|') if best and status != 'RESOLVE' else [],
            'alternatives': [
                {'brand_id': r['brand_id'],
                 'label': f"{r['base_brand']} {r['modifier']} {r['strength']}{r['unit']}".replace('  ', ' ').strip(),
                 'score': sc}
                for sc, r in scored[1:4]
            ],
        }


if __name__ == '__main__':
    r = Resolver()
    tests = [
        "dolo 650", "dolo six fifty", "DOLO six five zero",
        "pan d", "pan 40", "pantop 40",
        "zerodol sp", "zerodol p",
        "azithral 500", "azithril 500", "asithral",
        "telma 40", "telma h",
        "amlong 5", "amifru 5",
        "glycomet 500", "metrogyl 400",
        "tramazac 50", "trazonil 25",
        "augmentin 625", "augmentin duo",
        "xyzol", "shelcal", "montair lc",
        "blahmycin 200",
    ]
    for t in tests:
        res = r.resolve(t)
        alt = res['alternatives'][0]['label'] if res['alternatives'] else '-'
        print(f"{t:22} {res['status']:8} {str(res['matched']):26} "
              f"next: {alt:22} ({res['reason']})")
