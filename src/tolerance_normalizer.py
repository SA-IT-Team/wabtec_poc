"""ToleranceNormalizer: post-processes whatever tolerance shape the extraction model returned into
a consistent {nominal, upper, lower, unit} regardless of source notation (FR-12).
"""
from __future__ import annotations

from src.models import ExtractedBalloon, ToleranceType


class ToleranceNormalizer:
    # POC simplification: a single default general-tolerance band applied when a balloon is
    # flagged GENERAL but the model didn't resolve an explicit value. Production (FR-16) resolves
    # this against the drawing's actual general-tolerance block instead of a hard-coded constant.
    DEFAULT_GENERAL_TOLERANCE = 0.1

    def normalize(self, balloon: ExtractedBalloon) -> ExtractedBalloon:
        if balloon.tolerance_type is None or balloon.nominal_value is None:
            return balloon

        if balloon.tolerance_type == ToleranceType.BILATERAL:
            balloon = self._enforce_bilateral_signs(balloon)
        elif balloon.tolerance_type == ToleranceType.GENERAL and balloon.upper_tol is None:
            balloon = balloon.model_copy(
                update={
                    "upper_tol": self.DEFAULT_GENERAL_TOLERANCE,
                    "lower_tol": -self.DEFAULT_GENERAL_TOLERANCE,
                    "notes": (
                        (balloon.notes or "")
                        + " [general tolerance applied by default; verify against title block]"
                    ).strip(),
                }
            )
        return balloon

    @staticmethod
    def _enforce_bilateral_signs(balloon: ExtractedBalloon) -> ExtractedBalloon:
        updates = {}
        if balloon.upper_tol is not None and balloon.upper_tol < 0:
            updates["upper_tol"] = abs(balloon.upper_tol)
        if balloon.lower_tol is not None and balloon.lower_tol > 0:
            updates["lower_tol"] = -abs(balloon.lower_tol)
        return balloon.model_copy(update=updates) if updates else balloon

    def normalize_all(self, balloons: list[ExtractedBalloon]) -> list[ExtractedBalloon]:
        return [self.normalize(b) for b in balloons]
