"""Per-user outcome model: learns which kinds of postings convert for THIS
candidate and turns that into a bounded "track record" score line.

See docs/plans/2026-10-21-conversion-scoring-design.md.

Two signals, each a smoothed naive-Bayes log-odds over interpretable features
(track, seniority, portal, company, band strength, fired fit rules, ...):

- conversion: did an application get a response? Positive = reached phone
  screen / interview / offer (even if later rejected). Negative = rejected
  without reaching a screen, or applied and silent for GHOSTED_DAYS (half
  weight; "applied" is also set by clicking Apply, which is not proof of a
  submission).
- interest: what the candidate keeps (saved / applied / progressed) versus
  what they hide.

Every feature effect is shrunk towards the candidate's overall rate by a
Beta prior. A job's effects are summed and divided by sqrt(k) (the features
are correlated, so a plain naive-Bayes sum overcounts), squashed with tanh,
and scaled by a confidence ramp n / (n + RAMP). A signal stays off until it
has MIN_PER_CLASS evidence on both sides, so a new user, or one with only
rejections so far, keeps their scores unchanged.

Pure Python (no numpy) so it runs anywhere app.py does. Examples come from
database.get_outcome_examples(), which survives the 7-day job retention sweep.
"""
import hashlib
import json
import math
from datetime import datetime, timedelta

# Bounds (points) for each learned line.
CONVERSION_MAX = 12
INTEREST_MAX = 5

GHOSTED_DAYS = 21
GHOSTED_WEIGHT = 0.5

# Beta-prior strengths. BASE_RATE is the callback rate assumed before any data
# (cold applications in India rarely beat ~10%).
BASE_RATE = 0.10
GLOBAL_PRIOR = 5.0
FEATURE_PRIOR = 5.0
# Confidence ramp: an example count of RAMP gives half the full effect.
RAMP = 10.0
# Features seen in fewer (weighted) examples than this are ignored.
MIN_SUPPORT = 2.0
# A signal needs at least this much (weighted) evidence on EACH side before it
# adjusts anything: with only rejections (or only callbacks) there is no
# contrast, and every feature of the roles the candidate targets would read as
# negative, dragging their whole target segment down.
MIN_PER_CLASS = 2.0

_STAGE_SCREEN = 3     # applied_status: 3 phone screen, 4 interview, 5 offer
_STATUS_REJECTED = 6

_FEATURE_LABELS = {
    "track": "{} track", "seniority": "{} roles", "portal": "via {}",
    "company": "at {}", "target": "target companies", "remote": "{} roles",
    "fit": "{} roles", "band": "{}", "title": "title match {}",
}


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)[:19])
    except ValueError:
        return None


def labels(example, now=None):
    """(conversion, interest) labels for one example; each is (y, weight) or
    None when the example says nothing about that signal."""
    now = now or datetime.now()
    status = int(example.get("status") or 0)
    best = int(example.get("max_status") or 0)
    hidden = bool(example.get("hidden"))

    conversion = None
    if best >= _STAGE_SCREEN or status in (3, 4, 5):
        conversion = (1, 1.0)
    elif status == _STATUS_REJECTED:
        conversion = (0, 1.0)
    elif status == 1:
        applied = _parse_date(example.get("applied_date") or example.get("updated_at"))
        if applied and now - applied >= timedelta(days=GHOSTED_DAYS):
            conversion = (0, GHOSTED_WEIGHT)

    interest = None
    if hidden and status == 0:
        interest = (0, 1.0)
    elif status in (1, 2, 3, 4, 5, 6) or best >= _STAGE_SCREEN:
        interest = (1, 1.0)
    return conversion, interest


def _seniority(role):
    r = (role or "").lower()
    for word, bucket in (("chief", "executive"), ("head", "head"), ("director", "head"),
                         ("vp", "vp"), ("vice president", "vp"), ("principal", "lead"),
                         ("lead", "lead"), ("senior", "senior"), ("sr", "senior"),
                         ("associate", "junior"), ("junior", "junior"), ("intern", "junior")):
        if f" {word} " in f" {r.replace(',', ' ').replace('.', ' ')} ":
            return bucket
    return "mid"


def features(job, bd):
    """Interpretable feature keys ("kind:value") for a job + its breakdown."""
    out = {f"seniority:{_seniority(job.get('role'))}"}
    if bd.get("track"):
        out.add(f"track:{bd['track']}")
    portal = (job.get("portal") or "").strip().lower()
    if portal:
        out.add(f"portal:{portal}")
    company = (job.get("company") or "").strip().lower()
    if company:
        out.add(f"company:{company}")
    remote = (job.get("remote_status") or "").strip().lower()
    if remote:
        out.add(f"remote:{remote}")
    title = bd.get("title") or 0
    if title:
        out.add("title:" + ("strong" if title >= 30 else "partial"))
    for band in bd.get("bands") or []:
        key, label = band[0], band[1]
        pts = bd.get(key) or 0
        # Only present levels: "absent" describes nothing about the posting
        # and generalises spuriously (see the module docstring).
        if pts:
            out.add(f"band:{label} " + ("required" if pts >= band[2] else "mentioned"))
    if bd.get("no_domain_penalty"):
        # Carries neither differentiator: one explicit property, unlike the
        # per-band absences above.
        out.add(f"band:{bd.get('penalty_label') or 'no differentiator'}")
    for adj in bd.get("adjustments") or []:
        if adj[0] == "target_company":
            out.add("target:yes")
        elif adj[0] not in ("conversion", "interest"):
            out.add(f"fit:{adj[1]}")
    return out


def _logit(p):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def _describe(feature):
    kind, _, value = feature.partition(":")
    return _FEATURE_LABELS.get(kind, "{}").format(value)


class _Signal:
    """Smoothed naive-Bayes log-odds over feature keys for one label set."""

    def __init__(self, rows, max_points):
        self.max_points = max_points
        self.n = sum(w for _f, _y, w in rows)
        pos = sum(w * y for _f, y, w in rows)
        self.active = pos >= MIN_PER_CLASS and self.n - pos >= MIN_PER_CLASS
        self.p0 = (pos + GLOBAL_PRIOR * BASE_RATE) / (self.n + GLOBAL_PRIOR)
        counts = {}
        for feats, y, w in rows:
            for f in feats:
                c = counts.setdefault(f, [0.0, 0.0])
                c[0] += w * y
                c[1] += w
        base = _logit(self.p0)
        self.effects = {
            f: _logit((p + FEATURE_PRIOR * self.p0) / (n + FEATURE_PRIOR)) - base
            for f, (p, n) in counts.items() if n >= MIN_SUPPORT
        }

    def adjust(self, feats):
        hits = [(f, self.effects[f]) for f in feats if f in self.effects]
        if not hits or not self.active:
            return 0, []
        z = sum(e for _f, e in hits) / math.sqrt(len(hits))
        confidence = self.n / (self.n + RAMP)
        points = round(self.max_points * math.tanh(z) * confidence)
        hits.sort(key=lambda h: -abs(h[1]))
        return points, hits


class OutcomeModel:
    """Built from a user's outcome examples; .adjust(job, bd) returns the
    learned breakdown lines ([key, label, points, evidence]) for a job."""

    def __init__(self, conversion_rows, interest_rows, fingerprint):
        self.conversion = _Signal(conversion_rows, CONVERSION_MAX) if conversion_rows else None
        self.interest = _Signal(interest_rows, INTEREST_MAX) if interest_rows else None
        self.fingerprint = fingerprint
        self.n_conversion = len(conversion_rows)
        self.n_interest = len(interest_rows)

    def adjust(self, job, bd):
        if bd.get("irrelevant"):
            return []
        feats = features(job, bd)
        lines = []
        for key, label, signal in (("conversion", "Your outcomes", self.conversion),
                                   ("interest", "Your picks", self.interest)):
            if signal is None:
                continue
            points, hits = signal.adjust(feats)
            if not points:
                continue
            same_sign = [h for h in hits if (h[1] > 0) == (points > 0)][:2]
            verb = "like" if points > 0 else "unlike"
            evidence = f"{verb} " + ", ".join(_describe(f) for f, _e in same_sign)
            lines.append([key, label, points, evidence])
        return lines

    def insights(self, limit=5):
        """Top positive / negative learned effects, for display."""
        out = {}
        for key, signal in (("conversion", self.conversion), ("interest", self.interest)):
            if signal is None:
                continue
            ranked = sorted(signal.effects.items(), key=lambda kv: kv[1])
            out[key] = {
                "examples": round(signal.n, 1),
                "rate": round(signal.p0, 3),
                "active": signal.active,
                "helps": [[_describe(f), round(e, 2)] for f, e in reversed(ranked[-limit:]) if e > 0],
                "hurts": [[_describe(f), round(e, 2)] for f, e in ranked[:limit] if e < 0],
            }
        return out


def build(examples, cv_data, preferences, now=None):
    """Build an OutcomeModel from database.get_outcome_examples() rows.

    Each example's job snapshot is re-scored with the current deterministic
    scorer so its features (bands, fit rules) match the jobs being scored.
    """
    import scorer  # lazy: scorer never imports this module

    conversion_rows, interest_rows, stamp = [], [], []
    for ex in examples or []:
        conv, inter = labels(ex, now=now)
        if conv is None and inter is None:
            continue
        job = ex.get("job") or {}
        if isinstance(job, str):
            try:
                job = json.loads(job)
            except ValueError:
                job = {}
        _score, bd = scorer.deterministic_score(job, cv_data, preferences)
        if bd.get("irrelevant"):
            continue
        feats = features(job, bd)
        if conv is not None:
            conversion_rows.append((feats, conv[0], conv[1]))
        if inter is not None:
            interest_rows.append((feats, inter[0], inter[1]))
        stamp.append(f"{ex.get('job_id')}:{conv}:{inter}")
    fingerprint = ""
    if stamp:
        fingerprint = hashlib.sha1("|".join(sorted(stamp)).encode()).hexdigest()[:10]
    return OutcomeModel(conversion_rows, interest_rows, fingerprint)
