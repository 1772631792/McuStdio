"""Numerically stable FFT and harmonic analysis for live-watch samples."""

from __future__ import annotations

import math

import numpy as np


DEFAULT_FFT_SETTINGS = {
    "window": "hann",
    "fft_size": 0,
    "remove_dc": True,
    "display_max_hz": 0.0,
    "fundamental_min_hz": 0.5,
    "fundamental_max_hz": 0.0,
    "harmonic_count": 10,
    "harmonic_search_bins": 2,
    "scale": "percent",
    "db_floor": -100.0,
}


WINDOW_LABELS = {
    "hann": "Hann（通用）",
    "flattop": "Flat Top（幅值优先）",
    "hamming": "Hamming",
    "blackman": "Blackman",
    "rectangular": "矩形窗（整周期信号）",
}


def normalize_fft_settings(settings=None):
    """Return validated settings while remaining compatible with saved state."""
    source = settings if isinstance(settings, dict) else {}
    result = dict(DEFAULT_FFT_SETTINGS)
    if source.get("window") in WINDOW_LABELS:
        result["window"] = source["window"]
    try:
        size = int(source.get("fft_size", 0))
    except (TypeError, ValueError):
        size = 0
    result["fft_size"] = size if size in (0, 128, 256, 512, 1024, 2048, 4096, 8192) else 0
    result["remove_dc"] = bool(source.get("remove_dc", True))
    for key in ("display_max_hz", "fundamental_min_hz", "fundamental_max_hz", "db_floor"):
        try:
            value = float(source.get(key, result[key]))
        except (TypeError, ValueError):
            value = result[key]
        result[key] = value if math.isfinite(value) else result[key]
    result["display_max_hz"] = max(0.0, result["display_max_hz"])
    result["fundamental_min_hz"] = max(0.0, result["fundamental_min_hz"])
    result["fundamental_max_hz"] = max(0.0, result["fundamental_max_hz"])
    result["db_floor"] = min(-20.0, max(-180.0, result["db_floor"]))
    try:
        result["harmonic_count"] = min(30, max(2, int(source.get("harmonic_count", 10))))
        result["harmonic_search_bins"] = min(8, max(0, int(source.get("harmonic_search_bins", 2))))
    except (TypeError, ValueError):
        result["harmonic_count"] = 10
        result["harmonic_search_bins"] = 2
    result["scale"] = source.get("scale") if source.get("scale") in ("percent", "amplitude", "dbc") else "percent"
    return result


def _periodic_window(name, size):
    phase = 2.0 * np.pi * np.arange(size, dtype=np.float64) / float(size)
    if name == "rectangular":
        return np.ones(size, dtype=np.float64)
    if name == "hamming":
        return 0.54 - 0.46 * np.cos(phase)
    if name == "blackman":
        return 0.42 - 0.5 * np.cos(phase) + 0.08 * np.cos(2.0 * phase)
    if name == "flattop":
        return (0.21557895 - 0.41663158 * np.cos(phase)
                + 0.277263158 * np.cos(2.0 * phase)
                - 0.083578947 * np.cos(3.0 * phase)
                + 0.006947368 * np.cos(4.0 * phase))
    return 0.5 - 0.5 * np.cos(phase)


def _interpolated_peak(frequencies, amplitudes, index):
    """Quadratic peak interpolation for a less bin-dependent frequency estimate."""
    index = int(index)
    if index <= 0 or index >= len(amplitudes) - 1:
        return float(frequencies[index]), float(amplitudes[index])
    left, center, right = (float(amplitudes[index - 1]), float(amplitudes[index]),
                           float(amplitudes[index + 1]))
    denominator = left - 2.0 * center + right
    offset = 0.0 if abs(denominator) < 1e-30 else 0.5 * (left - right) / denominator
    offset = min(0.5, max(-0.5, offset))
    bin_width = float(frequencies[1] - frequencies[0])
    peak_amplitude = center - 0.25 * (left - right) * offset
    return float(frequencies[index]) + offset * bin_width, max(0.0, peak_amplitude)


def analyze_spectrum(timestamps_ms, values, settings=None):
    """Build a coherent-gain-corrected one-sided spectrum and harmonic table."""
    options = normalize_fft_settings(settings)
    timestamps = np.asarray(timestamps_ms, dtype=np.float64)
    samples = np.asarray(values, dtype=np.float64)
    count = min(timestamps.size, samples.size)
    timestamps, samples = timestamps[-count:], samples[-count:]
    finite = np.isfinite(timestamps) & np.isfinite(samples)
    timestamps, samples = timestamps[finite], samples[finite]
    if timestamps.size > 1:
        increasing = np.r_[True, np.diff(timestamps) > 0.0]
        timestamps, samples = timestamps[increasing], samples[increasing]

    requested = int(options["fft_size"])
    if requested:
        if timestamps.size < requested:
            return {"ok": False, "reason": f"等待采样：{timestamps.size}/{requested} 点"}
        fft_size = requested
    else:
        available = min(int(timestamps.size), 4096)
        fft_size = 1 << (available.bit_length() - 1) if available else 0
    if fft_size < 64:
        return {"ok": False, "reason": f"等待采样：{timestamps.size}/64 点"}

    timestamps = timestamps[-fft_size:]
    samples = samples[-fft_size:]
    duration_s = (float(timestamps[-1]) - float(timestamps[0])) / 1000.0
    if duration_s <= 0.0:
        return {"ok": False, "reason": "采样时间戳无效"}
    sample_rate = (fft_size - 1) / duration_s
    if not math.isfinite(sample_rate) or sample_rate <= 0.0:
        return {"ok": False, "reason": "无法计算实际采样率"}

    uniform_time = np.linspace(float(timestamps[0]), float(timestamps[-1]), fft_size)
    uniform_samples = np.interp(uniform_time, timestamps, samples)
    if options["remove_dc"]:
        uniform_samples = uniform_samples - float(np.mean(uniform_samples))
    window = _periodic_window(options["window"], fft_size)
    coherent_sum = float(np.sum(window))
    if abs(coherent_sum) < 1e-12:
        return {"ok": False, "reason": "窗函数增益无效"}

    spectrum = np.fft.rfft(uniform_samples * window)
    amplitudes = 2.0 * np.abs(spectrum) / abs(coherent_sum)
    amplitudes[0] *= 0.5
    if fft_size % 2 == 0:
        amplitudes[-1] *= 0.5
    frequencies = np.fft.rfftfreq(fft_size, d=1.0 / sample_rate)
    resolution = sample_rate / fft_size
    nyquist = sample_rate * 0.5

    search_min = max(float(options["fundamental_min_hz"]), resolution * 0.5)
    configured_max = float(options["fundamental_max_hz"])
    search_max = min(configured_max, nyquist) if configured_max > 0.0 else nyquist
    search_indices = np.flatnonzero((frequencies >= search_min) & (frequencies <= search_max))
    if not search_indices.size:
        return {"ok": False, "reason": "基波搜索范围内没有频点"}
    fundamental_index = int(search_indices[np.argmax(amplitudes[search_indices])])
    fundamental_frequency, fundamental_amplitude = _interpolated_peak(
        frequencies, amplitudes, fundamental_index
    )
    if fundamental_amplitude <= max(float(np.max(amplitudes)) * 1e-12, 1e-15):
        return {"ok": False, "reason": "未检测到有效基波"}

    harmonics = []
    search_bins = int(options["harmonic_search_bins"])
    for order in range(1, int(options["harmonic_count"]) + 1):
        target = fundamental_frequency * order
        if target > nyquist + resolution * 0.5:
            break
        center = int(round(target / resolution))
        lower = max(1, center - search_bins)
        upper = min(len(amplitudes) - 1, center + search_bins)
        if lower > upper:
            break
        region = np.arange(lower, upper + 1, dtype=np.int64)
        peak_index = int(region[np.argmax(amplitudes[region])])
        frequency, amplitude = _interpolated_peak(frequencies, amplitudes, peak_index)
        if order == 1:
            frequency, amplitude = fundamental_frequency, fundamental_amplitude
        harmonics.append({
            "order": order,
            "frequency_hz": frequency,
            "amplitude": amplitude,
            "percent": amplitude / fundamental_amplitude * 100.0,
        })
    thd = math.sqrt(sum(item["amplitude"] ** 2 for item in harmonics[1:])) / fundamental_amplitude * 100.0
    return {
        "ok": True,
        "frequencies": frequencies,
        "amplitudes": amplitudes,
        "sample_rate_hz": sample_rate,
        "resolution_hz": resolution,
        "fft_size": fft_size,
        "nyquist_hz": nyquist,
        "fundamental_frequency_hz": fundamental_frequency,
        "fundamental_amplitude": fundamental_amplitude,
        "harmonics": harmonics,
        "thd_percent": thd,
        "window": options["window"],
    }


def spectrum_display_values(amplitudes, fundamental_amplitude, scale, db_floor=-100.0):
    values = np.asarray(amplitudes, dtype=np.float64)
    reference = max(float(fundamental_amplitude), 1e-300)
    if scale == "amplitude":
        return values
    ratios = values / reference
    if scale == "dbc":
        return np.maximum(20.0 * np.log10(np.maximum(ratios, 1e-300)), float(db_floor))
    return ratios * 100.0
