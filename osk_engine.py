"""Suggestion / autocorrect / swipe-typing engine for the Omarchy OSK.

Pure Python, no dependencies. The bridge feeds it the current layout's key
centers and the language; it answers three questions:
  suggest(prefix)      -> likely completions while a word is being typed
  correct(word)        -> a fix for a just-committed word, or None
  decode_swipe(path)   -> candidate words for a glide across the letters

Dictionaries are AnySoftKeyboard's AOSP wordlists (word + frequency),
downloaded once per language into ~/.config/omarchy/osk/dict/<lang>.txt.
The swipe decoder is SHARK^2-shaped: prune the lexicon by the path's start /
end keys and length, then score candidates by how closely the resampled
finger path matches the word's ideal path through the key centers, blended
with word frequency.
"""
import gzip, io, math, os, re, threading, urllib.request

DICT_DIR = os.path.expanduser("~/.config/omarchy/osk/dict")
RAW = "https://raw.githubusercontent.com/AnySoftKeyboard/LanguagePack/master/languages/%s/pack/dictionary/%s"
SOURCES = {
    "english":  ("english",  "aosp.combined.gz"),
    "spain":    ("spain",    "aosp.combined"),
    "german":   ("german",   "de_wordlist.combined.gz"),
    "french":   ("french",   "aosp.combined.gz"),
    "hebrew":   ("hebrew",   "aosp.combined.gz"),
    "greek":    ("greek",    "aosp.combined.gz"),
    "russian2": ("russian2", "aosp.combined.gz"),
    "arabic":   ("arabic",   "lulua.combined.gz"),
    "persian":  ("persian",  "PersianPrebuild.xml"),
}
MAX_WORDS = 80000          # cap per language, sorted by frequency
CORR_POOL = 40000          # typo corrections only target common words
SWIPE_POOL = 50000

def _parse_combined(text):
    words = {}
    for ln in text.splitlines():
        m = re.match(r'\s*word=([^,]+),f=(\d+)', ln)
        if m:
            w, f = m.group(1), int(m.group(2))
            if w and " " not in w and (w not in words or words[w] < f):
                words[w] = f
    return words

def _parse_xml(text):
    words = {}
    for m in re.finditer(r'<w\s+[^>]*f="(\d+)"[^>]*>([^<]+)</w>', text):
        f, w = int(m.group(1)), m.group(2).strip()
        if w and " " not in w and (w not in words or words[w] < f):
            words[w] = f
    return words

def fetch_dict(lang, log=lambda *_: None):
    """Download + convert one language; returns the cache path or None."""
    os.makedirs(DICT_DIR, exist_ok=True)
    path = os.path.join(DICT_DIR, lang + ".txt")
    if os.path.exists(path):
        return path
    src = SOURCES.get(lang)
    if not src:
        return None
    url = RAW % src
    try:
        raw = urllib.request.urlopen(url, timeout=60).read()
        if src[1].endswith(".gz"):
            raw = gzip.decompress(raw)
        text = raw.decode("utf-8", "replace")
        words = _parse_xml(text) if src[1].endswith(".xml") else _parse_combined(text)
        out = sorted(words.items(), key=lambda kv: -kv[1])[:MAX_WORDS]
        with open(path, "w", encoding="utf-8") as f:
            for w, fr in out:
                f.write("%s\t%d\n" % (w, fr))
        log("dict ready", lang, len(out))
        return path
    except Exception as exc:
        log("dict fetch failed", lang, str(exc))
        return None


class Engine:
    def __init__(self, log=lambda *_: None):
        self.log = log
        self.lang = None
        self.words = {}          # word -> freq (original case)
        self.lower = {}          # lower -> (word, freq) best-cased
        self.by_first = {}       # first lower letter -> [(lowerword, word, freq)] freq-sorted
        self.keys = {}           # lower letter -> (x, y)
        self.unit = 60.0
        self.neigh = {}          # letter -> set of adjacent letters
        self.loading = None

    # ---- dictionary ---------------------------------------------------------
    def set_lang(self, lang, on_ready=None):
        if lang == self.lang and self.words:
            return
        def work():
            path = fetch_dict(lang, self.log)
            if not path:
                return
            words, lower, by_first = {}, {}, {}
            with open(path, encoding="utf-8") as f:
                for ln in f:
                    try:
                        w, fr = ln.rstrip("\n").split("\t")
                        fr = int(fr)
                    except ValueError:
                        continue
                    words[w] = fr
                    lw = w.lower()
                    if lw not in lower or lower[lw][1] < fr:
                        lower[lw] = (w, fr)
            for lw, (w, fr) in lower.items():
                by_first.setdefault(lw[0], []).append((lw, w, fr))
            for k in by_first:
                by_first[k].sort(key=lambda t: -t[2])
            self.words, self.lower, self.by_first, self.lang = words, lower, by_first, lang
            self.log("dict loaded", lang, len(lower))
            if on_ready:
                on_ready(lang, len(lower))
        self.loading = threading.Thread(target=work, daemon=True)
        self.loading.start()

    # ---- keymap -------------------------------------------------------------
    def set_keymap(self, keys, unit):
        self.keys = {k.lower(): (float(v[0]), float(v[1])) for k, v in keys.items()}
        self.unit = float(unit) or 60.0
        self.neigh = {}
        r = self.unit * 1.6
        for a, pa in self.keys.items():
            self.neigh[a] = {b for b, pb in self.keys.items()
                             if a != b and (pa[0]-pb[0])**2 + (pa[1]-pb[1])**2 < r*r}

    # ---- completions --------------------------------------------------------
    def suggest(self, prefix, n=3):
        if not prefix or not self.by_first:
            return []
        p = prefix.lower()
        out = []
        for lw, w, fr in self.by_first.get(p[0], []):
            if lw.startswith(p) and lw != p:
                out.append(w)
                if len(out) >= n:
                    break
        return out

    # ---- autocorrect --------------------------------------------------------
    def known(self, word):
        return word in self.words or word.lower() in self.lower

    def correct(self, word):
        if len(word) < 2 or not self.by_first or self.known(word):
            return None
        lw = word.lower()
        first = {lw[0]} | self.neigh.get(lw[0], set())
        best, best_score = None, 1e9
        maxdist = 1.2 if len(lw) <= 4 else 2.0
        for f0 in first:
            bucket = self.by_first.get(f0, [])
            for cand_l, cand, fr in bucket[:CORR_POOL // max(1, len(first))]:
                if abs(len(cand_l) - len(lw)) > 2:
                    continue
                d = self._dl(lw, cand_l, maxdist + 0.001)
                if d > maxdist:
                    continue
                score = d - 0.006 * min(fr, 255)
                if score < best_score:
                    best_score, best = score, cand
        if best is None:
            return None
        # keep the user's capitalization style
        if word[0].isupper():
            best = best[0].upper() + best[1:]
        return None if best == word else best

    def _dl(self, a, b, cutoff):
        """Damerau-Levenshtein with keyboard-adjacency-weighted substitution."""
        la, lb = len(a), len(b)
        prev2, prev, cur = None, list(x * 1.0 for x in range(lb + 1)), None
        for i in range(1, la + 1):
            cur = [i * 1.0] + [0.0] * lb
            rmin = cur[0]
            for j in range(1, lb + 1):
                if a[i-1] == b[j-1]:
                    sub = 0.0
                elif b[j-1] in self.neigh.get(a[i-1], ()):
                    sub = 0.55
                else:
                    sub = 1.1
                v = min(prev[j] + 1.0, cur[j-1] + 1.0, prev[j-1] + sub)
                if prev2 is not None and i > 1 and j > 1 and a[i-1] == b[j-2] and a[i-2] == b[j-1]:
                    v = min(v, prev2[j-2] + 0.6)
                cur[j] = v
                if v < rmin:
                    rmin = v
            if rmin > cutoff:
                return cutoff + 1
            prev2, prev = prev, cur
        return prev[lb]

    # ---- swipe decoding -----------------------------------------------------
    @staticmethod
    def _resample(path, n=32):
        if len(path) < 2:
            return [tuple(path[0])] * n if path else []
        total = 0.0
        segs = []
        for i in range(1, len(path)):
            d = math.dist(path[i-1], path[i])
            segs.append(d)
            total += d
        if total == 0:
            return [tuple(path[0])] * n
        step = total / (n - 1)
        out = [tuple(path[0])]
        acc, i = 0.0, 1
        px, py = path[0]
        while len(out) < n - 1 and i < len(path):
            d = math.dist((px, py), path[i])
            if acc + d >= step:
                t = (step - acc) / d if d else 0
                px, py = px + (path[i][0]-px)*t, py + (path[i][1]-py)*t
                out.append((px, py))
                acc = 0.0
            else:
                acc += d
                px, py = path[i]
                i += 1
        while len(out) < n:
            out.append(tuple(path[-1]))
        return out

    def _ideal(self, word):
        pts, last = [], None
        for c in word:
            if c == last:
                continue
            p = self.keys.get(c)
            if p is None:
                return None
            pts.append(p)
            last = c
        return pts if len(pts) >= 1 else None

    @staticmethod
    def _norm(pts):
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        s = max(max(abs(p[0]-cx) for p in pts), max(abs(p[1]-cy) for p in pts), 1e-6)
        return [((p[0]-cx)/s, (p[1]-cy)/s) for p in pts]

    def decode_swipe(self, path, n=4):
        if not self.keys or not self.by_first or len(path) < 3:
            return []
        rp = self._resample(path, 32)
        u = self.unit
        def near(pt, radius):
            out = set()
            for c, p in self.keys.items():
                if (p[0]-pt[0])**2 + (p[1]-pt[1])**2 < radius*radius:
                    out.add(c)
            return out
        starts = near(rp[0], u * 0.95) or near(rp[0], u * 1.4)
        starts = sorted(starts, key=lambda c: math.dist(self.keys[c], rp[0]))
        ends = near(rp[-1], u * 0.95) or near(rp[-1], u * 1.4)
        if not starts or not ends:
            return []
        plen = sum(math.dist(rp[i-1], rp[i]) for i in range(1, 32))
        cands = []
        for f0 in starts:
            got = 0
            for lw, w, fr in self.by_first.get(f0, [])[:SWIPE_POOL // max(1, len(starts))]:
                if len(lw) < 2 or lw[-1] not in ends:
                    continue
                ideal = self._ideal(lw)
                if not ideal or len(ideal) < 2:
                    continue
                ilen = sum(math.dist(ideal[i-1], ideal[i]) for i in range(1, len(ideal)))
                if ilen < plen * 0.35 or ilen > plen * 2.6:
                    continue
                cands.append((lw, w, fr, ideal))
                got += 1
                if got >= 900 or len(cands) >= 2600:
                    break
        scored = []
        nrp = self._norm(rp)
        for lw, w, fr, ideal in cands:
            ri = self._resample(ideal, 32)
            loc = sum(math.dist(rp[i], ri[i]) for i in range(32)) / 32 / u
            nri = self._norm(ri)
            shp = sum(math.dist(nrp[i], nri[i]) for i in range(32)) / 32
            score = 0.55 * loc + 1.4 * shp - 0.0035 * min(fr, 255)
            scored.append((score, w))
        scored.sort(key=lambda t: t[0])
        return [w for _, w in scored[:n]]
