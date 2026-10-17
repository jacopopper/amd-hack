import math
from statistics import median


def _distance(segment):
    return math.dist(segment.point_a, segment.point_b)


def validate_calibration(payload):
    """Fit metres per raw scene unit, then check it against held-out measurements."""
    fit = payload.fit_segments
    holdout = payload.holdout_segments
    scale_factor = median(segment.actual_distance_m / _distance(segment) for segment in fit)
    fit_errors = [abs(_distance(segment) * scale_factor - segment.actual_distance_m) / segment.actual_distance_m
                  for segment in fit]
    if max(fit_errors) > 0.15:
        raise ValueError("Fit measurements disagree by more than 15%; calibration was not saved")

    holdout_errors = [abs(_distance(segment) * scale_factor - segment.actual_distance_m) / segment.actual_distance_m
                      for segment in holdout]
    if max(holdout_errors) > payload.maximum_holdout_relative_error:
        raise ValueError("Independent holdout measurement failed; calibration was not saved")

    segments = []
    for role, values, errors in (("fit", fit, fit_errors), ("holdout", holdout, holdout_errors)):
        for segment, error in zip(values, errors, strict=True):
            item = segment.model_dump(mode="json")
            item.update({"role": role, "raw_distance": _distance(segment),
                         "predicted_distance_m": _distance(segment) * scale_factor,
                         "relative_error": error})
            segments.append(item)
    return {"scale_factor_m_per_unit": scale_factor,
            "fit_max_relative_error": max(fit_errors),
            "holdout_max_relative_error": max(holdout_errors),
            "segments": segments}
