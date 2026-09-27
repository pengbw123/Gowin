import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List

import numpy as np


WAVETABLE_POINTS = 2048
SAMPLE_RATE = 50000000.0 / 16.0 / 64.0
FILTER_LUT_POINTS = 256
FILTER_LUT_MIN_HZ = 80.0
FILTER_LUT_MAX_HZ = 18000.0


@dataclass
class Envelope:
    attack_ms: float = 80.0
    decay_ms: float = 900.0
    sustain: float = 0.45
    release_ms: float = 1300.0
    attack_curve: float = 0.15
    decay_curve: float = -0.25
    release_curve: float = -0.35

    @classmethod
    def from_dict(cls, value):
        allowed = set(cls.__dataclass_fields__.keys())
        return cls(**{k: value[k] for k in value if k in allowed})


@dataclass
class FilterSettings:
    enabled: bool = True
    follow_amplitude_envelope: bool = True
    min_cutoff_hz: float = 700.0
    max_cutoff_hz: float = 12000.0
    envelope_amount: float = 1.0
    pressure_amount: float = 0.0
    envelope: Envelope = field(default_factory=lambda: Envelope(
        attack_ms=15.0,
        decay_ms=700.0,
        sustain=0.25,
        release_ms=1000.0,
        attack_curve=0.2,
        decay_curve=-0.4,
        release_curve=-0.4,
    ))

    @classmethod
    def from_dict(cls, value):
        result = cls()
        for key in ("enabled", "follow_amplitude_envelope", "min_cutoff_hz",
                    "max_cutoff_hz", "envelope_amount", "pressure_amount"):
            if key in value:
                setattr(result, key, value[key])
        if "envelope" in value:
            result.envelope = Envelope.from_dict(value["envelope"])
        return result


@dataclass
class Tone:
    name: str = "New Tone"
    wavetable: List[int] = field(default_factory=list)
    harmonics: List[float] = field(default_factory=lambda: [1.0] + [0.0] * 15)
    harmonic_phases: List[float] = field(default_factory=lambda: [0.0] * 16)
    amplitude_envelope: Envelope = field(default_factory=Envelope)
    filter: FilterSettings = field(default_factory=FilterSettings)
    master_gain: float = 0.72
    metadata: Dict[str, str] = field(default_factory=dict)

    def ensure_valid(self):
        if len(self.wavetable) != WAVETABLE_POINTS:
            self.wavetable = generate_wavetable(self.harmonics, self.harmonic_phases).tolist()
        self.wavetable = [int(max(-32768, min(32767, int(v)))) for v in self.wavetable]
        self.harmonics = ([float(v) for v in self.harmonics] + [0.0] * 16)[:16]
        self.harmonic_phases = ([float(v) for v in self.harmonic_phases] + [0.0] * 16)[:16]
        self.master_gain = float(max(0.0, min(1.0, self.master_gain)))
        for envelope in (self.amplitude_envelope, self.filter.envelope):
            envelope.attack_ms = float(max(0.1, envelope.attack_ms))
            envelope.decay_ms = float(max(0.1, envelope.decay_ms))
            envelope.release_ms = float(max(0.1, envelope.release_ms))
        self.amplitude_envelope.sustain = float(max(0.0, min(1.0, self.amplitude_envelope.sustain)))
        self.filter.envelope.sustain = float(max(0.0, min(1.0, self.filter.envelope.sustain)))
        self.filter.min_cutoff_hz = float(max(FILTER_LUT_MIN_HZ, min(FILTER_LUT_MAX_HZ, self.filter.min_cutoff_hz)))
        self.filter.max_cutoff_hz = float(max(self.filter.min_cutoff_hz, min(FILTER_LUT_MAX_HZ, self.filter.max_cutoff_hz)))

    def to_dict(self):
        self.ensure_valid()
        data = asdict(self)
        data["format"] = "gowin-tone-v2"
        data["wavetable_format"] = "signed-q1.15"
        data["wavetable_points"] = WAVETABLE_POINTS
        data["sample_rate_hz"] = SAMPLE_RATE
        return data

    @classmethod
    def from_dict(cls, value):
        tone = cls(
            name=value.get("name", "Loaded Tone"),
            wavetable=value.get("wavetable", []),
            harmonics=value.get("harmonics", [1.0] + [0.0] * 15),
            harmonic_phases=value.get("harmonic_phases", [0.0] * 16),
            amplitude_envelope=Envelope.from_dict(value.get("amplitude_envelope", {})),
            filter=FilterSettings.from_dict(value.get("filter", {})),
            master_gain=value.get("master_gain", 0.72),
            metadata=value.get("metadata", {}),
        )
        tone.ensure_valid()
        return tone

    def save(self, path):
        Path(path).write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path):
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def generate_wavetable(harmonics, phases=None):
    amplitudes = np.asarray(list(harmonics), dtype=np.float64)
    if phases is None:
        phases = np.zeros_like(amplitudes)
    phases = np.asarray(list(phases), dtype=np.float64)
    if phases.size < amplitudes.size:
        phases = np.pad(phases, (0, amplitudes.size - phases.size), mode="constant")

    phase = np.arange(WAVETABLE_POINTS, dtype=np.float64) / WAVETABLE_POINTS
    wave = np.zeros(WAVETABLE_POINTS, dtype=np.float64)
    for index, amplitude in enumerate(amplitudes):
        harmonic = index + 1
        wave += amplitude * np.sin(2.0 * math.pi * harmonic * phase + phases[index])
    wave -= np.mean(wave)
    peak = float(np.max(np.abs(wave)))
    if peak < 1e-12:
        return np.zeros(WAVETABLE_POINTS, dtype=np.int16)
    wave = wave / peak * 0.96
    return np.rint(wave * 32767.0).clip(-32768, 32767).astype(np.int16)


def normalize_wavetable(values):
    wave = np.asarray(values, dtype=np.float64)
    if wave.size != WAVETABLE_POINTS:
        source = np.linspace(0.0, 1.0, wave.size, endpoint=False)
        target = np.linspace(0.0, 1.0, WAVETABLE_POINTS, endpoint=False)
        wave = np.interp(target, source, wave, period=1.0)
    wave -= np.mean(wave)
    peak = float(np.max(np.abs(wave)))
    if peak > 1e-12:
        wave = wave / peak * 0.96 * 32767.0
    return np.rint(wave).clip(-32768, 32767).astype(np.int16)


def cutoff_to_lut_index(cutoff_hz):
    cutoff = max(FILTER_LUT_MIN_HZ, min(FILTER_LUT_MAX_HZ, float(cutoff_hz)))
    ratio = math.log(cutoff / FILTER_LUT_MIN_HZ) / math.log(FILTER_LUT_MAX_HZ / FILTER_LUT_MIN_HZ)
    return int(round(ratio * (FILTER_LUT_POINTS - 1)))


def filter_alpha_lut():
    indices = np.arange(FILTER_LUT_POINTS, dtype=np.float64)
    frequencies = FILTER_LUT_MIN_HZ * np.power(
        FILTER_LUT_MAX_HZ / FILTER_LUT_MIN_HZ,
        indices / (FILTER_LUT_POINTS - 1),
    )
    alpha = 1.0 - np.exp(-2.0 * math.pi * frequencies / SAMPLE_RATE)
    alpha_q16 = np.rint(alpha * 65535.0).clip(0, 65535).astype(np.uint16)
    return frequencies, alpha_q16
