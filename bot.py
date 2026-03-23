"""
IIT Pokerbots 2026 — Sneak Peek Hold'em Bot  v3
================================================
Return convention (matching engine):
  fold  → "fold"
  check → "check"
  call  → "call"
  raise → int   (raise-TO amount, not raise-by)
  bid   → int

Street encoding: 0=preflop, 3=flop, 4=turn, 5=river
"""

import eval7
import random
from collections import Counter

# ── Constants ─────────────────────────────────────────────────────────────────
BIG_BLIND      = 20
SMALL_BLIND    = 10
STARTING_STACK = 5000
MC_SIMS_FULL   = 450   # full MC per decision
MC_SIMS_AUC    = 250   # faster MC for auction bid

RANK_MAP = {
    '2':2,'3':3,'4':4,'5':5,'6':6,'7':7,'8':8,'9':9,
    'T':10,'J':11,'Q':12,'K':13,'A':14
}

# Pre-build full deck as eval7.Card objects once at import time
_DECK_CARDS = [eval7.Card(r+s) for r in '23456789TJQKA' for s in 'cdhs']
_CARD_STR_TO_OBJ = {str(c): c for c in _DECK_CARDS}

# ── eval7 helpers ─────────────────────────────────────────────────────────────

def e7(card_str: str) -> eval7.Card:
    return _CARD_STR_TO_OBJ[card_str]

def hand_equity_mc(hole: list, board: list, peeked: str = None,
                   n: int = MC_SIMS_FULL) -> float:
    """
    Monte Carlo equity via eval7.
    eval7: lower score = BETTER hand.
    Pre-converts known cards to avoid per-sim object creation.
    If peeked is set, one of opponent's hole cards is fixed.
    """
    dead     = set(hole + board + ([peeked] if peeked else []))
    avail    = [c for c in _DECK_CARDS if str(c) not in dead]

    my_e7    = [e7(c) for c in hole]
    board_e7 = [e7(c) for c in board]
    peek_e7  = e7(peeked) if peeked else None

    need_board = 5 - len(board)
    need_opp   = 1 if peeked else 2

    wins = ties = 0
    for _ in range(n):
        random.shuffle(avail)
        draw = avail[:need_opp + need_board]

        if peeked:
            opp_e7    = [peek_e7, draw[0]]
            runout_e7 = draw[1:]
        else:
            opp_e7    = draw[:2]
            runout_e7 = draw[2:]

        full_board = board_e7 + runout_e7

        my_score  = eval7.evaluate(my_e7  + full_board)
        opp_score = eval7.evaluate(opp_e7 + full_board)

        if my_score < opp_score:    # lower = better
            wins += 1
        elif my_score == opp_score:
            ties += 1

    return (wins + 0.5 * ties) / n

def hand_equity_preflop(hole: list) -> float:
    """Calibrated heads-up equity table for pre-flop decisions."""
    r1 = RANK_MAP[hole[0][0]]
    r2 = RANK_MAP[hole[1][0]]
    hi, lo  = max(r1, r2), min(r1, r2)
    suited  = hole[0][1] == hole[1][1]
    gap     = hi - lo

    if gap == 0:  # pocket pair: 22=0.530 → AA=0.850
        return min(0.530 + (hi - 2) * 0.027, 0.850)

    base         = (hi / 14) * 0.52 + (lo / 14) * 0.18
    suited_bonus = 0.03 if suited else 0.0

    if gap == 1:  base += 0.04
    elif gap == 2: base += 0.02
    if gap > 3:   base -= 0.06
    if hi == 14:  base += 0.06

    cap = 0.73 if suited else 0.70
    return min(max(base + suited_bonus, 0.30), cap)

# ── Board texture ─────────────────────────────────────────────────────────────

def board_texture(board: list) -> dict:
    if not board:
        return {"flush_draw": False, "straight_draw": False,
                "paired": False, "danger": 0}

    suits  = [c[1] for c in board]
    ranks  = sorted([RANK_MAP[c[0]] for c in board])
    uniq   = sorted(set(ranks))

    flush_draw = max(Counter(suits).values()) >= 3

    straight_draw = False
    for i in range(len(uniq)):
        window = [r for r in uniq if uniq[i] <= r <= uniq[i] + 4]
        if len(window) >= 3:
            straight_draw = True
            break

    paired  = len(ranks) != len(set(ranks))
    danger  = int(flush_draw) + int(straight_draw) + int(paired)

    return {"flush_draw": flush_draw, "straight_draw": straight_draw,
            "paired": paired, "danger": danger}

# ── Stack-to-pot ratio ────────────────────────────────────────────────────────

def spr(my_stack: int, pot: int) -> float:
    """Stack-to-pot ratio. SPR < 4 → commit territory."""
    return my_stack / max(pot, 1)

# ── Pot odds & implied odds ───────────────────────────────────────────────────

def pot_odds(cost: int, pot: int) -> float:
    if cost <= 0:
        return 0.0
    return cost / (pot + cost)

def implied_pot_odds(cost: int, pot: int, my_stack: int, opp_stack: int,
                     street: int) -> float:
    """
    Discount pot odds on river (no more streets to extract value),
    improve them on flop/turn (more chips can come in).
    """
    base = pot_odds(cost, pot)
    if street == 5:          # river: no implied odds
        return base
    future_extract = min(my_stack, opp_stack) * 0.25   # conservative estimate
    return cost / (pot + cost + future_extract)

# ── Bet sizing ────────────────────────────────────────────────────────────────

def size_bet(equity: float, pot: int, my_stack: int, opp_stack: int) -> int:
    """Pure bet size (no existing call component)."""
    eff  = min(my_stack, opp_stack)
    if equity >= 0.82:  frac = 1.00    # pot-size / all-in
    elif equity >= 0.72: frac = 0.75
    elif equity >= 0.60: frac = 0.50
    else:                frac = 0.33   # bluff/semi-bluff
    amount = int(pot * frac)
    amount = max(amount, BIG_BLIND)
    return min(amount, eff)

def size_raise_to(equity: float, pot: int, cost: int,
                  my_stack: int, opp_stack: int) -> int:
    """Raise-to size when facing a bet (includes the call component)."""
    eff = min(my_stack, opp_stack)
    if equity >= 0.82:  frac = 1.20   # raise pot+
    elif equity >= 0.72: frac = 0.90
    elif equity >= 0.60: frac = 0.60
    else:                frac = 0.40  # bluff-raise small
    additional = int(pot * frac)
    additional = max(additional, BIG_BLIND)
    raise_to   = cost + additional
    raise_to   = max(raise_to, cost * 2 + BIG_BLIND)  # must be legal min
    return min(raise_to, eff)

# ── Legal action helpers ──────────────────────────────────────────────────────

def can(legal, action: str) -> bool:
    """
    Safely check if action is available regardless of whether legal is
    a set of strings, a set of Action class instances, or something else.
    """
    for a in legal:
        if action.lower() in str(type(a).__name__).lower():
            return True
        if action.lower() in str(a).lower():
            return True
    return False

# ── Opponent model ────────────────────────────────────────────────────────────

class OpponentModel:
    """
    Tracks: VPIP (voluntarily put chips in), PFR (preflop raise),
    aggression frequency per street, fold-to-bet frequency.
    All ratios smoothed with a prior so early rounds aren't noisy.
    """

    PRIOR = 5   # pseudocount — equivalent to 5 "average" observations

    def __init__(self):
        # raw counts
        self.pfr_count      = 0    # preflop raises
        self.vpip_count     = 0    # preflop calls or raises (not folds)
        self.agg_count      = 0    # postflop bets/raises
        self.passive_count  = 0    # postflop checks/calls
        self.fold_to_bet    = 0    # folded when we bet
        self.bet_count      = 0    # times we bet and opp had chance to respond
        self.rounds         = 0

    def update(self, terminal_state, active):
        self.rounds += 1
        ps = terminal_state.previous_state
        if not ps:
            return

        opp = 1 - active

        # PFR / VPIP proxy via final pip comparison
        # (crude but functional without per-action history)
        opp_pip_final = getattr(ps, 'pips', [0, 0])[opp]
        if opp_pip_final > BIG_BLIND:
            self.agg_count += 1
        elif opp_pip_final > 0:
            self.passive_count += 1

        if opp_pip_final >= BIG_BLIND * 2:
            self.pfr_count += 1
        if opp_pip_final >= BIG_BLIND:
            self.vpip_count += 1

    def _smooth(self, count: int, total: int, prior: float = 0.5) -> float:
        return (count + self.PRIOR * prior) / (total + self.PRIOR)

    @property
    def aggression_ratio(self) -> float:
        total = self.agg_count + self.passive_count
        return self._smooth(self.agg_count, total, 0.40)

    @property
    def pfr_ratio(self) -> float:
        return self._smooth(self.pfr_count, self.rounds, 0.25)

    def equity_threshold_adj(self) -> float:
        """
        Against aggressive opponents, require more equity to continue.
        Against passive ones, loosen up slightly.
        """
        r = self.aggression_ratio
        if r > 0.60:  return +0.06
        if r < 0.35:  return -0.04
        return 0.0

    def bluff_freq(self) -> float:
        """Bluff more into passive opponents, less into aggressive ones."""
        base = 0.10
        base += (0.50 - self.aggression_ratio) * 0.18
        return min(max(base, 0.03), 0.25)

    def value_bet_thin(self) -> bool:
        """True if opponent calls down too much → thin value bet."""
        return self.aggression_ratio < 0.35

# ── Main Bot ──────────────────────────────────────────────────────────────────

class Bot:

    def __init__(self):
        self.round_num    = 0
        self.opp          = OpponentModel()
        self.peeked_card  = None   # opponent card seen this round
        self.auction_done = False  # have we seen the auction this round?

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def handle_new_round(self, game_state, round_state, active):
        self.round_num   += 1
        self.peeked_card  = None
        self.auction_done = False

    def handle_round_over(self, game_state, terminal_state, active):
        self.opp.update(terminal_state, active)
        # NOTE: we capture peeked_card DURING get_action on post-auction streets,
        # not here, because this fires after the round ends.

    # ── Auction bid ────────────────────────────────────────────────────────

    def _auction_bid(self, hole: list, board: list,
                     pot: int, my_stack: int, opp_stack: int) -> int:
        """
        Second-price (Vickrey) auction theory:
          - Bid exactly your true value for the information.
          - In a second-price auction this is the weakly dominant strategy.
          - True value = expected chip gain from seeing one opponent card.

        Information value peaks at equity ≈ 0.5 (maximum uncertainty about
        who is ahead). Near 0 or 1 equity, peeking changes almost nothing.

        Additionally: if we are behind (eq < 0.45), information lets us
        make better fold decisions, so it's worth slightly more.
        """
        eq = hand_equity_mc(hole, board, n=MC_SIMS_AUC)
        u  = 1.0 - abs(eq - 0.5) * 2   # uncertainty: 1.0 at eq=0.5

        # Base value: fraction of pot we'd gain from perfect info
        info_value = pot * (0.06 + 0.28 * u)

        # Underdog bonus: knowing their card lets us fold cheaply when crushed
        if eq < 0.45:
            info_value *= 1.20
        # Slight favourite: knowing their card lets us extract more
        elif eq > 0.55:
            info_value *= 1.10

        # Second-price: bid true value, no overbid needed
        bid = int(info_value)
        bid = min(bid, min(my_stack, opp_stack))
        return max(bid, 0)

    # ── Core decision ──────────────────────────────────────────────────────

    def get_action(self, game_state, round_state, active):
        legal     = round_state.legal_actions()
        street    = round_state.street           # 0 / 3 / 4 / 5
        hole      = round_state.hands[active]    # ['Ah', 'Kd']
        board     = round_state.deck[:street]    # community cards
        my_stack  = round_state.stacks[active]
        opp_stack = round_state.stacks[1-active]
        pips      = round_state.pips             # [my_pip, opp_pip] this street

        # ── Pot reconstruction ────────────────────────────────────────────
        # round_state.pips = current street only.
        # Accumulate from previous streets via round_state.pot if available,
        # otherwise estimate from stacks vs starting chips.
        street_pot = sum(pips)
        prev_pot   = getattr(round_state, 'pot', 0) or 0
        pot        = street_pot + prev_pot
        if pot == 0:
            pot = street_pot  # fallback

        cost = pips[1-active] - pips[active]   # chips to call
        cost = max(cost, 0)

        # ── Capture peeked card from round_state ──────────────────────────
        # The engine should expose the peeked card somewhere on round_state
        # after the auction resolves. Common field names:
        if not self.peeked_card and street >= 3 and self.auction_done:
            for attr in ('peeked', 'peek', 'revealed_card', 'sneak_peek',
                         'auction_card', 'opp_card'):
                val = getattr(round_state, attr, None)
                if val:
                    self.peeked_card = str(val)
                    break

        # ── Auction detection ─────────────────────────────────────────────
        is_auction = can(legal, "bid")
        if is_auction:
            self.auction_done = True
            return self._auction_bid(hole, board, pot, my_stack, opp_stack)

        # ── Equity ───────────────────────────────────────────────────────
        if street == 0:
            equity = hand_equity_preflop(hole)
        else:
            equity = hand_equity_mc(hole, board, peeked=self.peeked_card)

        adj        = self.opp.equity_threshold_adj()
        eq_adj     = min(max(equity + adj, 0.0), 1.0)
        texture    = board_texture(board)
        danger     = texture["danger"]
        stack_pot  = spr(my_stack, pot) if pot > 0 else 99.0
        p_odds     = implied_pot_odds(cost, pot, my_stack, opp_stack, street)

        # Commit threshold: if SPR < 3, getting the money in with >50% is fine
        committed  = stack_pot < 3.0 and equity > 0.50

        # ── Pre-flop ─────────────────────────────────────────────────────
        if street == 0:
            return self._preflop(legal, equity, eq_adj, cost, pot,
                                 my_stack, opp_stack, active)

        # ── Post-flop ─────────────────────────────────────────────────────
        # Bluff decision: compute contextually (not blindly)
        do_bluff = (
            equity < 0.40 and
            street <= 4 and                       # don't bluff-raise river
            random.random() < self.opp.bluff_freq()
        )
        do_river_bluff = (
            street == 5 and
            equity < 0.38 and
            cost == 0 and                          # only bluff when checked to
            stack_pot > 2.0 and                    # don't bluff when too short
            random.random() < self.opp.bluff_freq() * 0.7
        )

        # ── Facing a bet ──────────────────────────────────────────────────
        if cost > 0:
            # Low SPR: just commit with any edge
            if committed:
                if can(legal, "raise"):
                    return size_raise_to(equity, pot, cost, my_stack, opp_stack)
                return "call"

            if eq_adj >= 0.70:
                # Strong hand: check-raise or 3-bet for value
                if can(legal, "raise"):
                    return size_raise_to(equity, pot, cost, my_stack, opp_stack)
                return "call"

            if eq_adj >= 0.52:
                # Decent hand: call if equity beats pot odds with margin
                if eq_adj > p_odds + 0.04:
                    return "call"
                # But thin-value call if opponent over-folds
                if self.opp.value_bet_thin() and eq_adj > p_odds:
                    return "call"
                return "fold"

            if do_bluff and can(legal, "raise"):
                # Semi-bluff raise with draws
                return size_raise_to(0.30, pot, cost, my_stack, opp_stack)

            if equity > p_odds + 0.02:   # direct pot-odds call with draws
                return "call"

            return "fold"

        # ── No bet facing (checked to us / first to act) ──────────────────
        # Check-raise opportunity: slowplay sets/strong hands occasionally
        # (We bet here, opponent calls, then on next action we can raise —
        # but for now: bet strong hands, check mediocre, check-raise is
        # handled by the "facing a bet" branch above after we check.)

        if eq_adj >= 0.68:
            if can(legal, "raise"):
                # Protect on dangerous boards; bigger sizing
                eff_eq = equity - 0.04 * danger
                return size_bet(eff_eq, pot, my_stack, opp_stack)

        elif eq_adj >= 0.54:
            # Thin value: bet ~50% of the time to balance range
            if can(legal, "raise"):
                if self.opp.value_bet_thin() or random.random() < 0.55:
                    return size_bet(equity, pot, my_stack, opp_stack)

        elif do_river_bluff and can(legal, "raise"):
            return size_bet(0.22, pot, my_stack, opp_stack)

        elif do_bluff and can(legal, "raise"):
            # Semi-bluff bet on flop/turn
            return size_bet(0.25, pot, my_stack, opp_stack)

        return "check"

    # ── Pre-flop helper ────────────────────────────────────────────────────

    def _preflop(self, legal, equity, eq_adj, cost, pot,
                 my_stack, opp_stack, active):
        is_bb = (active == 1)
        eff   = min(my_stack, opp_stack)

        if eq_adj >= 0.76:
            # Premium: raise to 4BB (or shove if short)
            if can(legal, "raise"):
                if eff <= BIG_BLIND * 15:           # short stack: shove
                    return eff
                return min(BIG_BLIND * 4, eff)
            return "call"

        if eq_adj >= 0.62:
            # Strong: raise to 3BB
            if can(legal, "raise"):
                raise_to = min(BIG_BLIND * 3, eff)
                return raise_to
            # Facing a raise: call if equity justifies it
            if cost > 0 and eq_adj > pot_odds(cost, pot) + 0.05:
                return "call"
            if cost <= BIG_BLIND:
                return "call"
            return "fold"

        if eq_adj >= 0.48:
            # Playable: limp or call small raise
            if cost == 0:
                return "check"
            if cost <= BIG_BLIND:
                return "call"
            if is_bb and cost == 0:
                return "check"
            if eq_adj > pot_odds(cost, pot) + 0.08:
                return "call"
            return "fold"

        # Weak hand
        if cost == 0:
            return "check"
        # Big blind gets a free look if no raise
        if is_bb and cost == 0:
            return "check"
        return "fold"
