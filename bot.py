

import eval7
import random

RANK_MAP = {'2':2,'3':3,'4':4,'5':5,'6':6,'7':7,'8':8,'9':9,
            'T':10,'J':11,'Q':12,'K':13,'A':14}


class Bot:
    def __init__(self):
        self.round = 0
        self.opp_aggression = 0
        self.opp_passive = 0

    def handle_new_round(self, game_state, round_state, active):
        self.round += 1

    def handle_round_over(self, game_state, terminal_state, active):
        if terminal_state.previous_state:
            last = terminal_state.previous_state
            if last.pips[1-active] > last.pips[active]:
                self.opp_aggression += 1
            else:
                self.opp_passive += 1

    # ---------- PREFLOP ----------
    def preflop_strength(self, hole):
        r1, s1 = hole[0][0], hole[0][1]
        r2, s2 = hole[1][0], hole[1][1]

        v1, v2 = RANK_MAP[r1], RANK_MAP[r2]

        if v1 == v2:
            return min(1, 0.8 + v1 / 25)

        suited = (s1 == s2)
        high, low = max(v1, v2), min(v1, v2)
        gap = high - low

        strength = (high / 14) * 0.7 + (low / 14) * 0.2

        if suited:
            strength += 0.05
        if gap == 1:
            strength += 0.05
        if gap > 3:
            strength -= 0.07

        return min(max(strength, 0), 1)

    # ---------- POSTFLOP ----------
    def postflop_strength(self, hole, board):
        cards = [eval7.Card(c) for c in hole + board]
        base = eval7.evaluate(cards) / 7462

        all_cards = hole + board

        # suits
        suits = [c[1] for c in all_cards]
        suit_counts = {}
        for s in suits:
            suit_counts[s] = suit_counts.get(s, 0) + 1

        max_suit = max(suit_counts.values())
        flush_draw = (max_suit == 4)
        made_flush = (max_suit >= 5)

        if flush_draw:
            base += 0.08
        if made_flush:
            base += 0.15

        # straight draws
        ranks = sorted(set([RANK_MAP[c[0]] for c in all_cards]))
        straight_draw = 0

        for i in range(len(ranks)):
            window = ranks[i:i+5]
            if len(window) < 4:
                continue

            if len(window) == 4 and window[-1] - window[0] == 3:
                straight_draw = max(straight_draw, 2)
            elif len(window) == 4 and window[-1] - window[0] == 4:
                straight_draw = max(straight_draw, 1)

        if straight_draw == 2:
            base += 0.08
        elif straight_draw == 1:
            base += 0.04

        # combo draw
        if flush_draw and straight_draw:
            base += 0.05

        return min(base, 1)

    # ---------- UNCERTAINTY ----------
    def uncertainty(self, strength):
        return 1 - abs(strength - 0.5) * 2

    # ---------- AUCTION ----------
    def auction_bid(self, strength, pot, stack):
        u = self.uncertainty(strength)

        # base Vickrey-style valuation
        value = pot * (0.05 + 0.25 * u)

        if strength > 0.85:
            value *= 0.6
        elif strength < 0.25:
            value *= 1.2

        return int(min(value, stack))

    # ---------- RANGE MODEL ----------
    def adjust_range(self):
        total = self.opp_aggression + self.opp_passive + 1
        ratio = self.opp_aggression / total

        if ratio > 0.6:
            return (0.4, 0.4, 0.2)
        else:
            return (0.2, 0.5, 0.3)

    def estimate_vs_range(self, strength):
        strong, medium, weak = self.adjust_range()

        win_vs_strong = strength * 0.6
        win_vs_medium = strength
        win_vs_weak = min(1, strength + 0.2)

        return (
            strong * win_vs_strong +
            medium * win_vs_medium +
            weak * win_vs_weak
        )

    # ---------- BOARD ----------
    def board_danger(self, board):
        if len(board) < 3:
            return 0

        suits = [c[1] for c in board]
        ranks = sorted([RANK_MAP[c[0]] for c in board])

        flush = max(suits.count(s) for s in suits) >= 3
        straight = (ranks[-1] - ranks[0]) <= 4

        return flush + straight

    # ---------- MAIN ----------
    def get_action(self, game_state, round_state, active):
        legal = round_state.legal_actions()

        street = round_state.street
        my_cards = round_state.hands[active]
        board = round_state.deck[:street]

        my_stack = round_state.stacks[active]
        opp_stack = round_state.stacks[1-active]

        pot = sum(round_state.pips)

        # ----- AUCTION -----
        if "bid" in legal:
            strength = self.postflop_strength(my_cards, board)
            return self.auction_bid(strength, pot, my_stack)

        # ----- STRENGTH -----
        if street < 3:
            strength = self.preflop_strength(my_cards)
        else:
            strength = self.postflop_strength(my_cards, board)

        win_prob = self.estimate_vs_range(strength)
        danger = self.board_danger(board)

        # ----- BLUFF -----
        bluff_prob = 0.08 + (self.opp_passive / (self.round + 1)) * 0.1
        bluff = strength < 0.35 and random.random() < bluff_prob

        # ----- ACTION -----
        if "raise" in legal:
            if win_prob > 0.75:
                return int(min(my_stack, opp_stack))

            elif win_prob > 0.6:
                return int(min(my_stack, opp_stack) * (0.5 - 0.1 * danger))

            elif bluff:
                return int(min(my_stack, opp_stack) * 0.4)

        if "call" in legal:
            if win_prob > (0.5 + 0.05 * danger):
                return "call"

        if "check" in legal:
            return "check"

        return "fold"