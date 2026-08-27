from src.models import ExtractedBalloon, ToleranceType
from src.tolerance_normalizer import ToleranceNormalizer


def test_passthrough_when_no_tolerance_type():
    normalizer = ToleranceNormalizer()
    balloon = ExtractedBalloon(balloon_number=1, nominal_value=10.0)

    result = normalizer.normalize(balloon)

    assert result == balloon


def test_bilateral_signs_are_corrected():
    normalizer = ToleranceNormalizer()
    balloon = ExtractedBalloon(
        balloon_number=1,
        nominal_value=25.4,
        tolerance_type=ToleranceType.BILATERAL,
        upper_tol=-0.05,  # model returned the wrong sign
        lower_tol=0.05,
    )

    result = normalizer.normalize(balloon)

    assert result.upper_tol == 0.05
    assert result.lower_tol == -0.05


def test_general_tolerance_default_applied_when_missing():
    normalizer = ToleranceNormalizer()
    balloon = ExtractedBalloon(
        balloon_number=1, nominal_value=10.0, tolerance_type=ToleranceType.GENERAL, upper_tol=None
    )

    result = normalizer.normalize(balloon)

    assert result.upper_tol == ToleranceNormalizer.DEFAULT_GENERAL_TOLERANCE
    assert result.lower_tol == -ToleranceNormalizer.DEFAULT_GENERAL_TOLERANCE
    assert "general tolerance applied by default" in result.notes


def test_general_tolerance_not_overridden_when_already_explicit():
    normalizer = ToleranceNormalizer()
    balloon = ExtractedBalloon(
        balloon_number=1,
        nominal_value=10.0,
        tolerance_type=ToleranceType.GENERAL,
        upper_tol=0.02,
        lower_tol=-0.02,
    )

    result = normalizer.normalize(balloon)

    assert result.upper_tol == 0.02
    assert result.lower_tol == -0.02


def test_normalize_all_applies_to_every_balloon():
    normalizer = ToleranceNormalizer()
    balloons = [
        ExtractedBalloon(balloon_number=1, nominal_value=1.0, tolerance_type=ToleranceType.BILATERAL, upper_tol=-1, lower_tol=1),
        ExtractedBalloon(balloon_number=2, nominal_value=None),
    ]

    result = normalizer.normalize_all(balloons)

    assert result[0].upper_tol == 1
    assert result[1].nominal_value is None
