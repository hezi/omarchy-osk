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
import gzip, hashlib, io, math, os, re, tempfile, threading, urllib.request, urllib.error
from urllib.parse import urlsplit

DICT_DIR = os.path.expanduser("~/.config/omarchy/osk/dict")

# Dictionaries are fetched from AnySoftKeyboard's LanguagePack, pinned to an
# immutable commit so the artifact can never change under us, and each file is
# verified against a known SHA-256 and exact byte size *before* it is used.
_PIN = "87e4c88d92b62afdb541e3bb0d16c6a47e63e835"
RAW = ("https://raw.githubusercontent.com/AnySoftKeyboard/LanguagePack/"
       + _PIN + "/languages/%s/pack/dictionary/%s")
# lang -> (subdir, filename, sha256, size_bytes)
SOURCES = {
    "english":  ("english",  "aosp.combined.gz",              "ffa9a0229ae81e4a942456d37c3db0cff984dd4b00980c42d4b87307e0fd97cf",   914064),
    "spain":    ("spain",    "aosp.combined",                 "bfcc7dd558dbb2251647f2f97124af555247a7bf168f38aa9fa8bc0a925b93af", 11091235),
    "german":   ("german",   "de_wordlist.combined.gz",       "38996899f92e386541677a5b9b3f8677f17d3c05116475ac71e22bcf5037022f",  1293426),
    "french":   ("french",   "aosp.combined.gz",              "4b6f00ef820514e2f354cfa9be885a49aa5c18926dd1ebb4991978816373805d",  1108437),
    "hebrew":   ("hebrew",   "aosp.combined.gz",              "678eff40afd23e6f0a8460b5600b5811d1798c05898a93045fad9c8b0aaa62bf",   465934),
    "greek":    ("greek",    "aosp.combined.gz",              "45ca1e21ff24322762f34b1feac4cb2427645a396621a1ea8aaebcf9217653e6",  1134961),
    "russian2": ("russian2", "aosp.combined.gz",              "3327b3da5a5cf23eebdbc124e62c2e5c5addaf1523cbcfa8b6311336a5c3eb10",  1397640),
    "arabic":   ("arabic",   "lulua.combined.gz",             "718240b5692cc342c5887eb49c0d182ff107e91445963e62679ee85a66b7d7d4", 27402426),
    "persian":  ("persian",  "prebuilt/PersianPrebuild.xml",  "ca2ea241b3c2bb081652e66f5c85037adcaf1b41918acabcf1d39a3fc16c0815",  5851214),
}
MAX_WORDS = 80000          # cap per language, sorted by frequency
CORR_POOL = 40000          # typo corrections only target common words
SWIPE_POOL = 50000
_MAX_DECOMPRESSED = 256 * 1024 * 1024   # gzip expansion ceiling (defense in depth)
_MAX_CACHE_BYTES = 64 * 1024 * 1024     # ignore a cache file larger than this
_MAX_CACHE_LINES = MAX_WORDS + 16

def _safe_lang(lang):
    # Allowlist only: the name also becomes a filename, so no separators/traversal.
    return lang if (lang in SOURCES and re.fullmatch(r"[a-z0-9]+", lang or "")) else None

class _HostLockedRedirect(urllib.request.HTTPRedirectHandler):
    """Follow redirects only within githubusercontent.com over https."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parts = urlsplit(newurl)
        host = parts.hostname or ""
        if parts.scheme != "https" or not (host == "raw.githubusercontent.com"
                                           or host.endswith(".githubusercontent.com")):
            raise urllib.error.HTTPError(newurl, code, "redirect host not allowed", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)

_opener = urllib.request.build_opener(_HostLockedRedirect)

def _download_verified(url, sha, size):
    with _opener.open(url, timeout=60) as r:
        data = r.read(size + 1)          # bounded: never stream more than one byte over
    if len(data) != size:
        raise ValueError("size mismatch (%d != %d)" % (len(data), size))
    if hashlib.sha256(data).hexdigest() != sha:
        raise ValueError("digest mismatch")
    return data

def _atomic_write(path, text):
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".osk-", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try: os.unlink(tmp)
        except OSError: pass
        raise

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
    """Download + verify + convert one language; returns the cache path or None."""
    lang = _safe_lang(lang)
    if not lang:
        return None
    os.makedirs(DICT_DIR, mode=0o700, exist_ok=True)
    path = os.path.join(DICT_DIR, lang + ".txt")
    if os.path.exists(path):
        return path
    subdir, fname, sha, size = SOURCES[lang]
    url = RAW % (subdir, fname)
    try:
        raw = _download_verified(url, sha, size)   # exact digest+size checked first
        if fname.endswith(".gz"):
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz:
                raw = gz.read(_MAX_DECOMPRESSED + 1)
            if len(raw) > _MAX_DECOMPRESSED:
                raise ValueError("decompressed too large")
        text = raw.decode("utf-8", "replace")
        words = _parse_xml(text) if fname.endswith(".xml") else _parse_combined(text)
        out = sorted(words.items(), key=lambda kv: -kv[1])[:MAX_WORDS]
        _atomic_write(path, "".join("%s\t%d\n" % (w, fr) for w, fr in out))
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
            try:
                if os.path.getsize(path) > _MAX_CACHE_BYTES:
                    self.log("dict cache too large, ignoring", lang)
                    return
            except OSError:
                return
            words, lower, by_first = {}, {}, {}
            with open(path, encoding="utf-8") as f:
                for _i, ln in enumerate(f):
                    if _i >= _MAX_CACHE_LINES:
                        break
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
