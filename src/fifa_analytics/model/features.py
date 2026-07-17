"""Maps a match's captured Summary-tab stats to per-attribute performance
scores, per position group.

Everything here works off the 18 stats Phase 1 actually captures (see
ocr/regions.PLAYER_SUMMARY_STAT_ORDER + match_rating) — not the richer
per-tab stats the original spec assumed. If deeper tabs get captured later
(duels, line breaks, shot types), this is the file that grows.

Two deliberate design rules:

1. **Evidence-gated scoring.** A striker who took zero shots produces NO
   shooting evidence that match — not a zero score. Otherwise quiet matches
   would drag attributes down in ways that say more about the flow of the
   game than the player. Each scorer returns None when its underlying
   actions didn't happen.

2. **Per-90 normalization.** Volume stats (passes, dribbles, distance) are
   scaled by minutes played before comparison, so a 46-minute substitute
   appearance isn't judged as half a performance.

Scores are in [0, 1] and get mapped to the card-rating scale (see
PERF_RATING_FLOOR/SPAN) by the blending layer in true_overall.py.
"""

ATTRIBUTES = ("pace", "shooting", "passing", "dribbling", "defending", "physical")

# Performance score 0.0 -> this rating; 1.0 -> floor + span. Chosen so a
# mediocre-but-active match reads in the 60s-70s and an exceptional one can
# push past 90, mirroring how card ratings distribute.
PERF_RATING_FLOOR = 45.0
PERF_RATING_SPAN = 55.0

# How much the in-game match rating (0-10, itself EA's holistic judgment of
# the performance) bleeds into every attribute that has evidence. Keeps a
# statistically quiet-but-effective match from reading as mediocre.
MATCH_RATING_BLEND = 0.3

# ---------------------------------------------------------------------------
# Position groups
# ---------------------------------------------------------------------------

POSITION_GROUP_MAP = {
    "GK": "GK",
    "SW": "CB", "CB": "CB", "LCB": "CB", "RCB": "CB",
    "LB": "FB", "RB": "FB", "LWB": "FB", "RWB": "FB",
    "CDM": "DM", "LDM": "DM", "RDM": "DM",
    "CM": "CM", "LCM": "CM", "RCM": "CM",
    "CAM": "AM", "LAM": "AM", "RAM": "AM",
    "LM": "W", "RM": "W", "LW": "W", "RW": "W", "LF": "W", "RF": "W",
    "ST": "ST", "CF": "ST", "LS": "ST", "RS": "ST",
}


def position_group(position: str | None) -> str:
    """SUB/RES/UNK (and anything unrecognized) fall back to GEN — generic
    weights — since the card data's club_position often just says where the
    player sits in the squad screen, not what they actually play."""
    return POSITION_GROUP_MAP.get((position or "").upper(), "GEN")


# Weights each sub-attribute contributes to the composite overall, per
# group — same idea as EA weighting a CB's overall differently from a ST's.
# Each row sums to 1.0.
#
# GK caveat: the Goalkeeping tab isn't captured in Phase 1, so a GK's
# performance evidence comes only from the generic stats (distribution =
# passing, sweeping = defending); their card attributes carry most of the
# weight until GK-specific capture exists.
OVERALL_WEIGHTS = {
    "GK":  {"pace": 0.05, "shooting": 0.05, "passing": 0.20, "dribbling": 0.05, "defending": 0.35, "physical": 0.30},
    "CB":  {"pace": 0.15, "shooting": 0.05, "passing": 0.15, "dribbling": 0.05, "defending": 0.35, "physical": 0.25},
    "FB":  {"pace": 0.25, "shooting": 0.05, "passing": 0.15, "dribbling": 0.10, "defending": 0.30, "physical": 0.15},
    "DM":  {"pace": 0.15, "shooting": 0.05, "passing": 0.20, "dribbling": 0.10, "defending": 0.30, "physical": 0.20},
    "CM":  {"pace": 0.15, "shooting": 0.10, "passing": 0.30, "dribbling": 0.15, "defending": 0.15, "physical": 0.15},
    "AM":  {"pace": 0.15, "shooting": 0.20, "passing": 0.25, "dribbling": 0.25, "defending": 0.05, "physical": 0.10},
    "W":   {"pace": 0.25, "shooting": 0.20, "passing": 0.15, "dribbling": 0.25, "defending": 0.05, "physical": 0.10},
    "ST":  {"pace": 0.20, "shooting": 0.30, "passing": 0.10, "dribbling": 0.20, "defending": 0.05, "physical": 0.15},
    "GEN": {"pace": 0.15, "shooting": 0.15, "passing": 0.20, "dribbling": 0.15, "defending": 0.20, "physical": 0.15},
}

# ---------------------------------------------------------------------------
# Per-match performance scoring
# ---------------------------------------------------------------------------

# Normalization caps: "a full-90 value at/above this is a 1.0". Chosen from
# realistic top-end per-90 outputs, so scores spread usefully across [0, 1].
CAP_PASSES_90 = 60.0
CAP_DRIBBLES_90 = 10.0
CAP_POSSESSION_WON_90 = 10.0
CAP_DISTANCE_90_KM = 12.0
CAP_SPRINTED_90_KM = 2.2


def _per90(value: float, minutes: float) -> float:
    if minutes <= 0:
        return 0.0
    return value * 90.0 / minutes


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def score_match_performance(stats: dict) -> dict:
    """stats: {stat_name: value} for one player's match (values may be None
    for failed OCR reads — treated as missing). Returns {attribute: score in
    [0,1]} containing ONLY attributes with evidence this match.
    """

    def get(name):
        value = stats.get(name)
        return float(value) if value is not None else None

    minutes = get("minutes_played_vs_team_avg") or 0.0
    if minutes <= 0:
        return {}

    scores: dict[str, float] = {}

    # Pace: sprint volume. (No breakaway/recovery-speed stats on this tab.)
    sprinted = get("distance_sprinted_vs_team_avg_km")
    if sprinted is not None:
        scores["pace"] = _clamp(_per90(sprinted, minutes) / CAP_SPRINTED_90_KM)

    # Shooting: accuracy + conversion, only if they actually shot.
    shots = get("shots")
    if shots and shots > 0:
        goals = get("goals") or 0.0
        accuracy = (get("shot_accuracy_pct") or 0.0) / 100.0
        conversion = _clamp(goals / shots)
        scores["shooting"] = _clamp(0.5 * accuracy + 0.5 * conversion)

    # Passing: accuracy, weighted up by volume; assists add a kicker.
    passes = get("passes")
    if passes and passes > 0:
        accuracy = (get("pass_accuracy_pct") or 0.0) / 100.0
        volume = _clamp(_per90(passes, minutes) / CAP_PASSES_90)
        assists = get("assists") or 0.0
        scores["passing"] = _clamp(accuracy * (0.6 + 0.4 * volume) + 0.15 * min(assists, 2.0))

    # Dribbling: success rate, weighted up by volume.
    dribbles = get("dribbles")
    if dribbles and dribbles > 0:
        success = (get("dribble_success_rate_pct") or 0.0) / 100.0
        volume = _clamp(_per90(dribbles, minutes) / CAP_DRIBBLES_90)
        scores["dribbling"] = _clamp(success * (0.6 + 0.4 * volume))

    # Defending: tackle success + ball-winning volume; fouls chip away.
    tackles = get("tackles") or 0.0
    possession_won = get("possession_won") or 0.0
    if tackles > 0 or possession_won > 0:
        parts = []
        if tackles > 0:
            parts.append((get("tackle_success_rate_pct") or 0.0) / 100.0)
        parts.append(_clamp(_per90(possession_won, minutes) / CAP_POSSESSION_WON_90))
        fouls = get("fouls_committed") or 0.0
        scores["defending"] = _clamp(sum(parts) / len(parts) - 0.05 * fouls)

    # Physical: work rate (distance) + winning the ball back.
    covered = get("distance_covered_vs_team_avg_km")
    if covered is not None:
        distance_score = _clamp(_per90(covered, minutes) / CAP_DISTANCE_90_KM)
        duel_score = _clamp(_per90(possession_won, minutes) / CAP_POSSESSION_WON_90)
        scores["physical"] = _clamp(0.65 * distance_score + 0.35 * duel_score)

    # Blend the holistic match rating into every attribute with evidence.
    rating = get("match_rating")
    if rating is not None and scores:
        rating_score = _clamp((rating - 5.0) / 4.0)  # 5.0 -> 0.0, 9.0+ -> 1.0
        for attr in scores:
            scores[attr] = (1 - MATCH_RATING_BLEND) * scores[attr] + MATCH_RATING_BLEND * rating_score

    return scores


def perf_score_to_rating(score: float) -> float:
    return PERF_RATING_FLOOR + PERF_RATING_SPAN * _clamp(score)
