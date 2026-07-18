"""The assistant's knowledge base: what every model in this system means
and how to use it. Written by hand to match what the code actually does —
if a model changes, change its explainer here in the same commit.

Split by topic so the context builder can send only what a question needs
(local models do better with short, relevant context)."""

EXPLAINERS = {
    "true_overall": """\
TRUE OVERALL — what it is and how to read it
- Card overall is EA's static rating. True overall is this system's living
  rating: the card value (used as a Bayesian prior) blended with the
  player's actual captured match performances.
- Blend weight: w = n_eff / (n_eff + 5), where n_eff is full-match
  equivalents of evidence. One match barely moves a rating (w≈0.17); ~5
  full matches means half card / half performance; a full season is
  dominated by performance.
- Per-match performance is evidence-gated: a striker who took no shots
  produces NO shooting evidence that match, not a zero. Quiet games don't
  drag ratings down; bad games do.
- Volume stats are per-90 normalized, so substitute appearances aren't
  judged as half-performances.
- confidence_score rises with evidence. Low confidence = the number is
  still mostly the card prior; treat differences of a point or two as
  noise until confidence grows.
- HOW TO USE IT: prefer true overall over card overall for selection once
  a player has several matches of history. A big positive delta
  (true - card) = over-performer the card undersells; big negative =
  card reputation their play isn't backing up.""",
    "best_xi": """\
BEST XI — what it is and how to read it
- Picks the optimal 11 by maximizing total (rating x positional fit) with
  the Hungarian algorithm — globally optimal, not greedy, so two good
  players never block each other from their shared natural slot.
- rating: latest true overall where the player has reviewed match history,
  card overall otherwise (marked card_only).
- Positional fit (familiarity): 1.0 in the player's natural group,
  discounted as the role gets further away (a CM in the DM slot loses
  little; a striker at CB loses a lot). 'effective' = rating x fit.
- HOW TO USE IT: 'best of all' compares every known formation by total
  effective rating. A player listed out of their natural group means the
  solver judged the discounted rating still beats the alternatives —
  usually a squad-depth gap worth fixing in the market.""",
    "xpts": """\
xPTS (expected points) — what it is and how to read it
- Each match's captured xG pair becomes win/draw/loss probabilities via
  independent Poisson goal models, then expected points:
  xPTS = 3*P(win) + 1*P(draw).
- Over a season, points minus xPTS = over/under-performance of underlying
  play. Positive = results flatter you (finishing hot streak, keeper
  heroics, luck); negative = playing better than results show.
- HOW TO USE IT: trust xPTS trends over 1-2 match noise. A negative delta
  with good xG numbers usually means keep the setup and let results
  regress upward; a big positive delta warns your results may cool off
  even if nothing changes.""",
    "tactics_fit": """\
TACTICAL FIT & TRANSFER SCORING — what it is and how to read it
- Every position group weights the six attributes differently (a CB's
  overall leans on defending/physical; a winger's on pace/dribbling).
- A tactic multiplies those weights (possession boosts passing/dribbling,
  counter_attack boosts pace, etc.), renormalized to sum to 1 — so a
  transfer target is scored for YOUR system, not in the abstract.
- Transfer targets must clear two bars: plausible for the position
  (familiarity >= 0.7 — a striker is never suggested for a CB hole) and an
  actual upgrade (tactic-weighted composite x familiarity beats the
  current starter's effective rating).
- Academy prospects use a different bar: growth room (potential well above
  current), young age, and a floor (>=60 overall) so they don't hurt the
  team if thrown in.
- Surplus (transfers out): bench players 5+ points below the starter of
  their own group — older ones are sale candidates, younger ones are
  loan/develop candidates.
- KNOWN GAP: goalkeepers never appear as transfer targets — the card
  dataset has no GK sub-attributes, so keepers can't be tactic-scored.""",
    "matchup": """\
OPPONENT MATCHUP — what it is and how to read it
- Both teams' best XIs are computed independently (each in its own best
  formation), then compared at unit level: goalkeeper / defense /
  midfield / attack average effective rating.
- Slot-by-slot comparison across different formations is misleading (a
  3-5-2 has no wingers), so unit deltas are the trustworthy signal.
- their_biggest_threats = their top 3 by effective rating;
  my_weakest_slots = your bottom 3 — where their threats meet your weak
  slots is where matches are lost.
- HOW TO USE IT: a big negative midfield delta suggests an extra central
  body (4-3-3 or 4-2-3-1 over 4-4-2); a negative defense delta against a
  strong attack unit suggests the more conservative shape and pace at
  fullback. The numbers rank options; the call is yours.""",
    "data_trust": """\
DATA TRUST BOUNDARIES — how the numbers get made
- Screenshots -> OCR -> human review (validation UI) -> models. Only
  REVIEWED captures feed true overalls and xPTS by default.
- rating_source card_only means no reviewed match history yet — the
  number is EA's card, not observed play.
- Regens (academy players unknown to the card dataset) have no prior:
  their ratings are pure performance estimates with low confidence until
  they accumulate matches.""",
}

TOPIC_KEYWORDS = {
    "true_overall": ("true overall", "card overall", "rating", "confidence", "delta", "overall", "form", "improv"),
    "best_xi": ("squad", "xi", "lineup", "line-up", "formation", "pick", "select", "team sheet", "bench", "start"),
    "xpts": ("xpts", "expected points", "xg", "over-perform", "underperform", "over perform", "under perform", "luck", "deserve", "points", "table", "record"),
    "tactics_fit": ("transfer", "sign", "buy", "sell", "scout", "target", "academy", "prospect", "loan", "surplus", "tactic", "possession", "counter", "press"),
    "matchup": ("against", "opponent", "vs", "versus", "matchup", "match up", "face", "facing", "play them", "beat"),
    "data_trust": ("model", "how does", "how do", "what does", "mean", "work", "trust", "accurate", "ocr", "review"),
}


def relevant_explainers(question: str) -> dict[str, str]:
    """Explainer sections whose keywords appear in the question; all of
    them when nothing matches (broad questions deserve the full picture)."""
    lowered = question.lower()
    hits = {
        topic: text
        for topic, text in EXPLAINERS.items()
        if any(keyword in lowered for keyword in TOPIC_KEYWORDS[topic])
    }
    return hits or dict(EXPLAINERS)
