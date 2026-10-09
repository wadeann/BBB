"""Candidate engine — full scanner, scorer, ranker, and top-N selector.

Architecture
------------
1. Scan tradable universe through enabled patterns (from PolicyEntry).
2. Filter candidates by liquidity, RS, sector resonance.
3. Score each pass-through candidate with the 6-dimension v4 scoring card.
4. Rank by score (desc), tie-break by pattern priority, then symbol.
5. Cap visible output at top 10; preserve full eligible list internally.
6. LLM structured-veto gate (timeout/error = no-op, never a buy signal).
"""

from statistics import mean
from typing import Any, Callable
import logging
import asyncio
from dataclasses import dataclass, field
import inspect

from .scoring import SCORING_SCHEMA_VERSION, score_all
from .pattern_registry import PatternRegistry
from .signal_engine import DeterministicSignalEngine
from ..utils import now_shanghai

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PATTERN_PRIORITY: dict[str, int] = {
    "triple_golden_cross": 1,
    "ma_convergence_breakout": 2,
    "high_volume_breakout": 3,
    "ma60_breakout_retest": 4,
    "single_bull_hold": 5,
    "ma5_momentum_pullback": 6,
    "low_volume_support_bull": 7,
    "rebound_confirmation": 8,
    "rebound_candidate": 9,
    "long_bull_day7": 10,
    "volume_price_divergence": 11,
    "shooting_star_high": 12,
    "ma20_break": 13,
    "ma_bearish_cut": 14,
    # intraday momentum (live MCP only)
    "morning_surge": 15,
    "vwap_hold": 16,
    "afternoon_breakout": 17,
    "vwap_break_warning": 50,
}

DEFAULT_MIN_VOLUME: float = 50_000_000.0  # 50M CNY daily turnover
DEFAULT_TOP_N: int = 10
DEFAULT_LLM_TIMEOUT: float = 5.0  # seconds

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class Candidate:
    """Single scored candidate produced by the engine.

    Mirrors the Wave 2 shared ``Candidate`` contract.
    """

    symbol: str
    pattern_id: str
    pattern_version: str
    score: float
    score_breakdown: dict[str, float]
    regime_at_signal: str
    theme_lifecycle: str
    sector: str
    sector_strength: str
    liquidity_score: float
    rs_score: float
    sector_resonance: float
    entry_price: float | None
    stop_price: float | None
    evidence_summary: dict
    rank: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "pattern_id": self.pattern_id,
            "pattern_version": self.pattern_version,
            "score": self.score,
            "score_breakdown": dict(self.score_breakdown),
            "regime_at_signal": self.regime_at_signal,
            "theme_lifecycle": self.theme_lifecycle,
            "sector": self.sector,
            "sector_strength": self.sector_strength,
            "liquidity_score": self.liquidity_score,
            "rs_score": self.rs_score,
            "sector_resonance": self.sector_resonance,
            "entry_price": self.entry_price,
            "stop_price": self.stop_price,
            "evidence_summary": dict(self.evidence_summary),
            "rank": self.rank,
        }


@dataclass
class ScannerResult:
    """Result of a full scan pass."""

    candidates: list[Candidate] = field(default_factory=list)
    top_n: list[Candidate] = field(default_factory=list)
    skipped_liquidity: int = 0
    skipped_rs: int = 0
    skipped_sector: int = 0
    llm_vetoed: int = 0
    total_scanned: int = 0
    as_of: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidates": [c.to_dict() for c in self.candidates],
            "top_n": [c.to_dict() for c in self.top_n],
            "skipped_liquidity": self.skipped_liquidity,
            "skipped_rs": self.skipped_rs,
            "skipped_sector": self.skipped_sector,
            "llm_vetoed": self.llm_vetoed,
            "total_scanned": self.total_scanned,
            "as_of": self.as_of,
        }


# ---------------------------------------------------------------------------
# LLM gate type alias
# ---------------------------------------------------------------------------

LlmGateFn = Callable[
    [list[Candidate], dict[str, Any]],
    "list[dict[str, Any]] | Awaitable[list[dict[str, Any]]]",
]
"""LLM gate signature: ``(candidates, market_context) -> list[veto_dict]``.

Each veto dict should have ``{"symbol": str, "veto": bool, "reason": str}``.
Raising or returning None is treated as no-op (no veto).
"""

# ---------------------------------------------------------------------------
# Candidate Engine
# ---------------------------------------------------------------------------


class CandidateEngine:
    """Full candidate scanning, scoring, ranking, and selection engine.

    Usage::

        engine = CandidateEngine(registry, min_volume=50_000_000)
        result = engine.scan(
            bars_by_symbol=bars,
            enabled_patterns=[("triple_golden_cross", "1.0.0"), ...],
            market_context=ctx,
            sector_map={...},
        )
    """

    def __init__(
        self,
        registry: PatternRegistry | None = None,
        *,
        min_volume: float = DEFAULT_MIN_VOLUME,
        top_n: int = DEFAULT_TOP_N,
        llm_gate: LlmGateFn | None = None,
        llm_timeout: float = DEFAULT_LLM_TIMEOUT,
    ) -> None:
        self._registry = registry or DeterministicSignalEngine.build_registry()
        self._engine = DeterministicSignalEngine()
        self._min_volume = min_volume
        self._top_n = top_n
        self._llm_gate = llm_gate
        self._llm_timeout = llm_timeout

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan(
        self,
        bars_by_symbol: dict[str, list[dict[str, Any]]],
        enabled_patterns: list[tuple[str, str]],
        market_context: dict[str, Any],
        *,
        sector_map: dict[str, dict[str, Any]] | None = None,
        rs_scores: dict[str, float] | None = None,
        liquidity_scores: dict[str, float] | None = None,
        signal_hints: dict[str, list[dict[str, Any]]] | None = None,
    ) -> ScannerResult:
        """Run a full candidate scan.

        Parameters
        ----------
        bars_by_symbol : dict
            Symbol -> list of OHLCV bars (most recent last).
        enabled_patterns : list of (pattern_id, version)
            From PolicyEntry key[2], key[1] for each enabled policy.
        market_context : dict
            Output of ``MarketContextBuilder.build()``.
        sector_map : dict, optional
            Symbol -> {sector, sector_rank, sector_strength, mainline, echelon_count, ...}
        rs_scores : dict, optional
            Symbol -> relative strength score [0, 1].
        liquidity_scores : dict, optional
            Symbol -> liquidity score [0, 1] (from turnover percentile).
        signal_hints : dict, optional
            Pre-computed signal hits per symbol to avoid re-scanning.

        Returns
        -------
        ScannerResult with candidates, top_n, and skip stats.
        """
        as_of = market_context.get("as_of", now_shanghai().isoformat())
        regime = market_context.get("regime", "UNKNOWN")
        lifecycle = market_context.get("theme_lifecycle", "UNKNOWN")
        sector_map = sector_map or {}
        rs_scores = rs_scores or {}
        liquidity_scores = liquidity_scores or {}
        signal_hints = signal_hints or {}
        enabled_set = set(enabled_patterns)

        result = ScannerResult(as_of=as_of)
        candidates: list[Candidate] = []
        total = 0

        for symbol, bars in bars_by_symbol.items():
            total += 1

            # Skip if no bars
            if not bars or len(bars) < 60:
                continue

            # --- Step 1: detect signals ---
            if symbol in signal_hints:
                hits = signal_hints[symbol]
            else:
                try:
                    hits = self._scan_symbol(symbol, bars, market_context)
                except Exception:
                    logger.warning("Scan failed for %s", symbol, exc_info=True)
                    continue

            # --- Step 2: filter to enabled patterns ---
            matched_hits = [h for h in hits if (h.get("pattern_id", ""), h.get("pattern_version", "")) in enabled_set]
            if not matched_hits:
                continue

            # --- Step 3: liquidity filter ---
            liq = liquidity_scores.get(symbol, self._infer_liquidity(bars))
            if liq < self._min_volume:
                result.skipped_liquidity += 1
                continue

            # --- Step 4: RS filter ---
            rs = rs_scores.get(symbol, 0.5)
            if rs <= 0.0:
                result.skipped_rs += 1
                continue

            # --- Step 5: sector data ---
            sinfo = sector_map.get(symbol, {})
            sector = sinfo.get("sector", "unknown")
            sector_strength = sinfo.get("sector_strength", "neutral")
            sector_rank = float(sinfo.get("rank", 0.0))
            sector_mainline = bool(sinfo.get("mainline", False))
            echelon_count = int(sinfo.get("echelon_count", 0))
            liquidity_score = min(1.0, liq / max(self._min_volume, 1))

            # --- Step 6: score each matched hit ---
            for hit in matched_hits:
                cand = self._build_candidate(
                    symbol=symbol,
                    hit=hit,
                    bars=bars,
                    market_context=market_context,
                    regime=regime,
                    lifecycle=lifecycle,
                    sector=sector,
                    sector_strength=sector_strength,
                    sector_rank=sector_rank,
                    sector_mainline=sector_mainline,
                    echelon_count=echelon_count,
                    rs_score=rs,
                    liquidity_score=liquidity_score,
                )
                candidates.append(cand)

        # --- Step 7: rank and cap ---
        self._rank_candidates(candidates)
        result.candidates = candidates

        # --- Step 8: LLM gate ---
        candidates = self._apply_llm_gate(candidates, market_context, result)

        # --- Step 9: top-N ---
        result.top_n = candidates[: self._top_n]
        result.total_scanned = total

        logger.info(
            "Scan complete: %d scanned, %d eligible, %d top-N (skipped: liq=%d rs=%d sec=%d llm=%d)",
            total,
            len(candidates),
            len(result.top_n),
            result.skipped_liquidity,
            result.skipped_rs,
            result.skipped_sector,
            result.llm_vetoed,
        )
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _scan_symbol(
        self,
        symbol: str,
        bars: list[dict[str, Any]],
        market_context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Run the deterministic signal engine for one symbol."""
        sector_strength = "unknown"
        return self._engine.scan(bars, market_regime=market_context.get("market_regime", "unknown"), sector_strength=sector_strength)

    def _infer_liquidity(self, bars: list[dict[str, Any]]) -> float:
        """Infer daily turnover (CNY) from bar data as a liquidity proxy."""
        if not bars:
            return 0.0
        recent = bars[-5:]
        turnovers = []
        for b in recent:
            vol = float(b.get("volume", 0))
            close = float(b.get("close", 0))
            turnovers.append(vol * close)
        return mean(turnovers) if turnovers else 0.0

    def _build_candidate(
        self,
        *,
        symbol: str,
        hit: dict[str, Any],
        bars: list[dict[str, Any]],
        market_context: dict[str, Any],
        regime: str,
        lifecycle: str,
        sector: str,
        sector_strength: str,
        sector_rank: float,
        sector_mainline: bool,
        echelon_count: int,
        rs_score: float,
        liquidity_score: float,
    ) -> Candidate:
        """Score a single hit and package it as a Candidate."""
        # Run scoring card
        scoring_result = score_all(
            bars,
            sector_rank=sector_rank,
            sector_mainline=sector_mainline,
            echelon_count=echelon_count,
        )

        # Entry/stop: synthetic from bar data
        entry_price = None
        stop_price = None
        if bars:
            entry_price = float(bars[-1].get("close", 0))
            # Simple support-based stop: 5% below MA20
            from .signal_engine import sma as _sma

            closes = [float(x.get("close", 0)) for x in bars]
            ma20 = _sma(closes, 20)
            if ma20 and ma20[-1] is not None:
                stop_price = round(float(ma20[-1]) * 0.95, 2)

        evidence = {
            "signal_strength": hit.get("strength", ""),
            "pattern_id": hit.get("pattern_id", ""),
            "pattern_version": hit.get("pattern_version", ""),
            "hit_evidence": hit.get("evidence", {}),
            "scoring_schema": SCORING_SCHEMA_VERSION,
        }

        return Candidate(
            symbol=symbol,
            pattern_id=hit.get("pattern_id", ""),
            pattern_version=hit.get("pattern_version", "1.0.0"),
            score=scoring_result["score"],
            score_breakdown=scoring_result["breakdown"],
            regime_at_signal=regime,
            theme_lifecycle=lifecycle,
            sector=sector,
            sector_strength=sector_strength,
            liquidity_score=round(liquidity_score, 4),
            rs_score=round(rs_score, 4),
            sector_resonance=round(min(1.0, sector_rank + 0.1 * echelon_count), 4),
            entry_price=entry_price,
            stop_price=stop_price,
            evidence_summary=evidence,
        )

    def _rank_candidates(self, candidates: list[Candidate]) -> None:
        """Sort candidates in place by score, then pattern priority, then symbol.

        Tie-break rules:
        1. Higher score wins.
        2. Same score → lower PATTERN_PRIORITY number wins (higher priority).
        3. Same priority → lexicographically smaller symbol wins.
        """
        candidates.sort(
            key=lambda c: (
                -c.score,
                PATTERN_PRIORITY.get(c.pattern_id, 99),
                c.symbol,
            )
        )
        for i, c in enumerate(candidates):
            c.rank = i + 1

    def _apply_llm_gate(
        self,
        candidates: list[Candidate],
        market_context: dict[str, Any],
        result: ScannerResult,
    ) -> list[Candidate]:
        """Apply the optional LLM veto gate.

        Rules:
        - If no LLM gate configured, pass through.
        - If LLM gate raises or times out → no-op (no vetoes applied).
        - LLM gate output: list of ``{"symbol": str, "veto": bool, "reason": str}``.
        - Only veto=True entries remove their symbol from the final list.
        - If any candidate from a symbol is vetoed, *all* candidates for that
          symbol are removed (veto applies to symbol, not individual pattern).
        """
        if not self._llm_gate or not candidates:
            return candidates

        try:
            if inspect.iscoroutinefunction(self._llm_gate):
                vetoes = asyncio.run(
                    asyncio.wait_for(
                        self._llm_gate(candidates, market_context),
                        timeout=self._llm_timeout,
                    )
                )
            else:
                vetoes = self._llm_gate(candidates, market_context)
        except asyncio.TimeoutError:
            logger.warning("LLM gate timed out after %.1fs — no veto applied", self._llm_timeout)
            return candidates
        except Exception:
            logger.warning("LLM gate error — no veto applied", exc_info=True)
            return candidates

        if not isinstance(vetoes, list):
            logger.warning("LLM gate returned non-list — no veto applied")
            return candidates

        vetoed_symbols: set[str] = set()
        for v in vetoes:
            if isinstance(v, dict) and v.get("veto") is True:
                sym = v.get("symbol", "")
                if sym:
                    vetoed_symbols.add(sym)

        if vetoed_symbols:
            result.llm_vetoed = len(vetoed_symbols)
            before = len(candidates)
            candidates = [c for c in candidates if c.symbol not in vetoed_symbols]
            logger.info("LLM veto removed %d/%d candidates (%d symbols)", before - len(candidates), before, len(vetoed_symbols))

        return candidates


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------

def build_engine(
    *,
    min_volume: float = DEFAULT_MIN_VOLUME,
    top_n: int = DEFAULT_TOP_N,
    llm_gate: LlmGateFn | None = None,
) -> CandidateEngine:
    """Build a fully-wired CandidateEngine with the default pattern registry."""
    registry = DeterministicSignalEngine.build_registry()
    return CandidateEngine(
        registry=registry,
        min_volume=min_volume,
        top_n=top_n,
        llm_gate=llm_gate,
    )
