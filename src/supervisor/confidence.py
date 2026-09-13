from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


# ============================================================
# DECISION
# ============================================================

class ReviewDecision(str, Enum):
    ACCEPT = "accept"
    REVIEW = "review"
    DECLINE = "decline"


# ============================================================
# RESULT
# ============================================================

@dataclass
class ConfidenceResult:
    score: float
    decision: ReviewDecision

    ocr_score: float
    extraction_score: float
    supervisor_score: float
    master_data_score: float
    financial_score: float

    reasons: list[str]


# ============================================================
# ENGINE
# ============================================================

class ConfidenceEngine:
    """
    Conservative confidence engine for financial automation.

    The final score combines:

        OCR quality
        +
        extraction quality
        +
        visual supervisor verification
        +
        master-data matching
        +
        financial validation

    IMPORTANT:

    A high weighted score can NEVER override a failed
    financial validation or unresolved required information.
    """

    def __init__(
        self,
        accept_threshold: float = 0.90,
        review_threshold: float = 0.70,
    ) -> None:

        if not 0.0 <= review_threshold <= 1.0:
            raise ValueError(
                "review_threshold must be between 0 and 1."
            )

        if not 0.0 <= accept_threshold <= 1.0:
            raise ValueError(
                "accept_threshold must be between 0 and 1."
            )

        if review_threshold > accept_threshold:
            raise ValueError(
                "review_threshold cannot be greater than "
                "accept_threshold."
            )

        self.accept_threshold = accept_threshold
        self.review_threshold = review_threshold

    # ========================================================
    # CLAMP
    # ========================================================

    @staticmethod
    def clamp(
        value: float,
    ) -> float:
        """
        Keep a confidence value within [0, 1].
        """

        try:
            value = float(value)

        except (TypeError, ValueError):
            return 0.0

        return max(
            0.0,
            min(1.0, value),
        )

    # ========================================================
    # RESULT FACTORY
    # ========================================================

    @staticmethod
    def _result(
        score: float,
        decision: ReviewDecision,
        ocr_score: float,
        extraction_score: float,
        supervisor_score: float,
        master_data_score: float,
        financial_score: float,
        reasons: list[str],
    ) -> ConfidenceResult:

        return ConfidenceResult(
            score=round(
                float(score),
                4,
            ),
            decision=decision,
            ocr_score=round(
                float(ocr_score),
                4,
            ),
            extraction_score=round(
                float(extraction_score),
                4,
            ),
            supervisor_score=round(
                float(supervisor_score),
                4,
            ),
            master_data_score=round(
                float(master_data_score),
                4,
            ),
            financial_score=round(
                float(financial_score),
                4,
            ),
            reasons=reasons,
        )

    # ========================================================
    # CALCULATE
    # ========================================================

    def calculate(
        self,
        ocr_score: float,
        extraction_score: float,
        supervisor_score: float,
        master_data_score: float,
        financial_score: float,
        has_unresolved_conflict: bool = False,
        has_missing_required_field: bool = False,
    ) -> ConfidenceResult:
        """
        Calculate final confidence and review decision.

        Weighted score:

            OCR             20%
            Extraction      20%
            Supervisor      25%
            Master Data     20%
            Financial       15%

        Hard safety rules are evaluated BEFORE acceptance.

        Automatic ACCEPT requires:

            final score >= 0.90
            AND
            financial score >= 0.90
            AND
            no unresolved conflict
            AND
            no missing required field
            AND
            master-data score >= 0.90
            AND
            supervisor score >= 0.90
            AND
            extraction score >= 0.90
            AND
            OCR score >= 0.90
        """

        reasons: list[str] = []

        # ----------------------------------------------------
        # Normalize scores
        # ----------------------------------------------------

        ocr_score = self.clamp(
            ocr_score
        )

        extraction_score = self.clamp(
            extraction_score
        )

        supervisor_score = self.clamp(
            supervisor_score
        )

        master_data_score = self.clamp(
            master_data_score
        )

        financial_score = self.clamp(
            financial_score
        )

        # ----------------------------------------------------
        # Weighted confidence
        # ----------------------------------------------------

        score = (
            0.20 * ocr_score
            + 0.20 * extraction_score
            + 0.25 * supervisor_score
            + 0.20 * master_data_score
            + 0.15 * financial_score
        )

        score = self.clamp(score)

        # ====================================================
        # HARD SAFETY CONDITIONS
        # ====================================================

        # ----------------------------------------------------
        # 1. Unresolved conflict
        # ----------------------------------------------------

        if has_unresolved_conflict:

            reasons.append(
                "Unresolved OCR/document conflict."
            )

            return self._result(
                score,
                ReviewDecision.REVIEW,
                ocr_score,
                extraction_score,
                supervisor_score,
                master_data_score,
                financial_score,
                reasons,
            )

        # ----------------------------------------------------
        # 2. Missing required field
        # ----------------------------------------------------

        if has_missing_required_field:

            reasons.append(
                "Required financial field is missing."
            )

            return self._result(
                score,
                ReviewDecision.REVIEW,
                ocr_score,
                extraction_score,
                supervisor_score,
                master_data_score,
                financial_score,
                reasons,
            )

        # ----------------------------------------------------
        # 3. Financial validation hard gate
        # ----------------------------------------------------

        if financial_score < self.accept_threshold:

            reasons.append(
                "Financial validation is below the "
                "automatic-posting threshold."
            )

            # Very weak financial validation means decline.
            if financial_score < self.review_threshold:

                reasons.append(
                    "Financial confidence is too low "
                    "for payable creation."
                )

                return self._result(
                    score,
                    ReviewDecision.DECLINE,
                    ocr_score,
                    extraction_score,
                    supervisor_score,
                    master_data_score,
                    financial_score,
                    reasons,
                )

            # Otherwise require manual review.
            return self._result(
                score,
                ReviewDecision.REVIEW,
                ocr_score,
                extraction_score,
                supervisor_score,
                master_data_score,
                financial_score,
                reasons,
            )

        # ----------------------------------------------------
        # 4. Master-data hard gate
        # ----------------------------------------------------

        if master_data_score < self.accept_threshold:

            reasons.append(
                "Master-data matching is below the "
                "automatic-posting threshold."
            )

            if master_data_score < self.review_threshold:

                reasons.append(
                    "Master-data confidence is too low "
                    "for payable creation."
                )

                return self._result(
                    score,
                    ReviewDecision.DECLINE,
                    ocr_score,
                    extraction_score,
                    supervisor_score,
                    master_data_score,
                    financial_score,
                    reasons,
                )

            return self._result(
                score,
                ReviewDecision.REVIEW,
                ocr_score,
                extraction_score,
                supervisor_score,
                master_data_score,
                financial_score,
                reasons,
            )

        # ----------------------------------------------------
        # 5. Supervisor hard gate
        # ----------------------------------------------------

        if supervisor_score < self.accept_threshold:

            reasons.append(
                "Visual supervisor verification is below "
                "the automatic-posting threshold."
            )

            if supervisor_score < self.review_threshold:

                reasons.append(
                    "Supervisor confidence is too low "
                    "for payable creation."
                )

                return self._result(
                    score,
                    ReviewDecision.DECLINE,
                    ocr_score,
                    extraction_score,
                    supervisor_score,
                    master_data_score,
                    financial_score,
                    reasons,
                )

            return self._result(
                score,
                ReviewDecision.REVIEW,
                ocr_score,
                extraction_score,
                supervisor_score,
                master_data_score,
                financial_score,
                reasons,
            )

        # ----------------------------------------------------
        # 6. Extraction gate
        # ----------------------------------------------------

        if extraction_score < self.accept_threshold:

            reasons.append(
                "Document extraction confidence is below "
                "the automatic-posting threshold."
            )

            if extraction_score < self.review_threshold:

                reasons.append(
                    "Extraction confidence is too low "
                    "for payable creation."
                )

                return self._result(
                    score,
                    ReviewDecision.DECLINE,
                    ocr_score,
                    extraction_score,
                    supervisor_score,
                    master_data_score,
                    financial_score,
                    reasons,
                )

            return self._result(
                score,
                ReviewDecision.REVIEW,
                ocr_score,
                extraction_score,
                supervisor_score,
                master_data_score,
                financial_score,
                reasons,
            )

        # ----------------------------------------------------
        # 7. OCR gate
        # ----------------------------------------------------

        if ocr_score < self.accept_threshold:

            reasons.append(
                "OCR quality is below the automatic-posting "
                "threshold."
            )

            if ocr_score < self.review_threshold:

                reasons.append(
                    "OCR quality is too low for payable creation."
                )

                return self._result(
                    score,
                    ReviewDecision.DECLINE,
                    ocr_score,
                    extraction_score,
                    supervisor_score,
                    master_data_score,
                    financial_score,
                    reasons,
                )

            return self._result(
                score,
                ReviewDecision.REVIEW,
                ocr_score,
                extraction_score,
                supervisor_score,
                master_data_score,
                financial_score,
                reasons,
            )

        # ====================================================
        # FINAL SCORE
        # ====================================================

        if score >= self.accept_threshold:

            reasons.append(
                "All verification signals satisfy the "
                "automatic-posting requirements."
            )

            return self._result(
                score,
                ReviewDecision.ACCEPT,
                ocr_score,
                extraction_score,
                supervisor_score,
                master_data_score,
                financial_score,
                reasons,
            )

        # ----------------------------------------------------
        # REVIEW
        # ----------------------------------------------------

        if score >= self.review_threshold:

            reasons.append(
                "Overall confidence is insufficient for "
                "automatic posting."
            )

            return self._result(
                score,
                ReviewDecision.REVIEW,
                ocr_score,
                extraction_score,
                supervisor_score,
                master_data_score,
                financial_score,
                reasons,
            )

        # ----------------------------------------------------
        # DECLINE
        # ----------------------------------------------------

        reasons.append(
            "Overall confidence is too low for payable creation."
        )

        return self._result(
            score,
            ReviewDecision.DECLINE,
            ocr_score,
            extraction_score,
            supervisor_score,
            master_data_score,
            financial_score,
            reasons,
        )


# ============================================================
# STANDALONE TEST
# ============================================================

def main() -> None:

    engine = ConfidenceEngine(
        accept_threshold=0.90,
        review_threshold=0.70,
    )

    print("=" * 70)
    print("CONFIDENCE ENGINE TEST")
    print("=" * 70)

    # --------------------------------------------------------
    # TEST 1: Everything strong
    # --------------------------------------------------------

    result = engine.calculate(
        ocr_score=0.95,
        extraction_score=0.94,
        supervisor_score=0.96,
        master_data_score=0.95,
        financial_score=1.00,
    )

    print("\nTEST 1 - ALL STRONG")
    print(f"Score:    {result.score:.4f}")
    print(f"Decision: {result.decision.value}")

    for reason in result.reasons:
        print(f"  - {reason}")

    # --------------------------------------------------------
    # TEST 2: Financial failure
    # --------------------------------------------------------

    result = engine.calculate(
        ocr_score=0.99,
        extraction_score=0.99,
        supervisor_score=0.99,
        master_data_score=0.99,
        financial_score=0.50,
    )

    print("\nTEST 2 - FINANCIAL FAILURE")
    print(f"Score:    {result.score:.4f}")
    print(f"Decision: {result.decision.value}")

    for reason in result.reasons:
        print(f"  - {reason}")

    # --------------------------------------------------------
    # TEST 3: Master-data review
    # --------------------------------------------------------

    result = engine.calculate(
        ocr_score=0.95,
        extraction_score=0.95,
        supervisor_score=0.95,
        master_data_score=0.80,
        financial_score=1.00,
    )

    print("\nTEST 3 - MASTER DATA REVIEW")
    print(f"Score:    {result.score:.4f}")
    print(f"Decision: {result.decision.value}")

    for reason in result.reasons:
        print(f"  - {reason}")

    # --------------------------------------------------------
    # TEST 4: Unresolved conflict
    # --------------------------------------------------------

    result = engine.calculate(
        ocr_score=0.99,
        extraction_score=0.99,
        supervisor_score=0.99,
        master_data_score=0.99,
        financial_score=1.00,
        has_unresolved_conflict=True,
    )

    print("\nTEST 4 - UNRESOLVED CONFLICT")
    print(f"Score:    {result.score:.4f}")
    print(f"Decision: {result.decision.value}")

    for reason in result.reasons:
        print(f"  - {reason}")

    # --------------------------------------------------------
    # TEST 5: Missing required field
    # --------------------------------------------------------

    result = engine.calculate(
        ocr_score=0.95,
        extraction_score=0.95,
        supervisor_score=0.95,
        master_data_score=0.95,
        financial_score=1.00,
        has_missing_required_field=True,
    )

    print("\nTEST 5 - MISSING REQUIRED FIELD")
    print(f"Score:    {result.score:.4f}")
    print(f"Decision: {result.decision.value}")

    for reason in result.reasons:
        print(f"  - {reason}")

    # --------------------------------------------------------
    # TEST 6: All weak
    # --------------------------------------------------------

    result = engine.calculate(
        ocr_score=0.40,
        extraction_score=0.40,
        supervisor_score=0.40,
        master_data_score=0.40,
        financial_score=0.40,
    )

    print("\nTEST 6 - ALL WEAK")
    print(f"Score:    {result.score:.4f}")
    print(f"Decision: {result.decision.value}")

    for reason in result.reasons:
        print(f"  - {reason}")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()