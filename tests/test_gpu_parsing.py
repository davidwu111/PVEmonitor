"""Tests for GPU metric parsing (rocm-smi, nvidia-smi, helpers)."""

import pytest
from pvemonitor.gpu import (
    _parse_rocm_text,
    _parse_rocm_json,
    _parse_nvidia_csv,
    _safe_float,
    _compute_vram_pct,
)


class TestRocmTextParsing:
    """Test parsing of rocm-smi text-mode output."""

    def test_parse_temperature(self):
        """Should extract temperature from rocm-smi text output."""
        stdout = "GPU[0] : Temperature (Edge): 48.0 C\nGPU[0] : GPU use (%): 87.0 %\n"
        result = _parse_rocm_text(stdout)
        assert result["gpu_temp_c"] == 48.0

    def test_parse_gpu_use(self):
        """Should extract GPU busy percentage."""
        stdout = "GPU[0] : Temperature (Edge): 48.0 C\nGPU[0] : GPU use (%): 87.0 %\n"
        result = _parse_rocm_text(stdout)
        assert result["gpu_busy_pct"] == 87.0

    def test_parse_power(self):
        """Should extract Average Graphics Package Power."""
        stdout = "GPU[0] : Average Graphics Package Power (W): 45.0 W\n"
        result = _parse_rocm_text(stdout)
        assert result["gpu_power_w"] == 45.0

    def test_empty_output(self):
        """Empty stdout should return only default name, rest None."""
        result = _parse_rocm_text("")
        assert result["gpu_temp_c"] is None
        assert result["gpu_busy_pct"] is None
        assert result["gpu_power_w"] is None
        assert result["gpu_vram_used_pct"] is None

    def test_no_recognizable_fields(self):
        """Garbage input should not crash."""
        result = _parse_rocm_text("random text\nnothing useful\n")
        assert result["gpu_name"] == "AMD GPU (rocm-smi)"


class TestRocmJsonParsing:
    """Test parsing of rocm-smi JSON output."""

    def test_single_gpu_dict(self):
        """Single GPU as a flat dict."""
        data = {
            "GPU use (%)": "87.0",
            "Temperature (Sensor edge) (C)": "48.0",
            "Average Graphics Package Power (W)": "45.0",
            "Card name": "AMD Radeon 780M",
            "VRAM (%)": "62",
        }
        result = _parse_rocm_json(data)
        assert result["gpu_name"] == "AMD Radeon 780M"
        assert result["gpu_busy_pct"] == 87.0
        assert result["gpu_temp_c"] == 48.0
        assert result["gpu_power_w"] == 45.0
        assert result["gpu_vram_used_pct"] == 62.0

    def test_unsupported_fields(self):
        """Unsupported marker strings should be converted to None."""
        data = {
            "GPU use (%)": "N/A",
            "Temperature (Sensor edge) (C)": "unsupported",
        }
        result = _parse_rocm_json(data)
        assert result["gpu_busy_pct"] is None
        assert result["gpu_temp_c"] is None

    def test_empty_data(self):
        """Empty input should return None values."""
        result = _parse_rocm_json({})
        assert all(v is None for v in result.values())


class TestSafeFloat:
    """Test _safe_float conversion."""

    def test_normal_float(self):
        assert _safe_float("48.0") == 48.0

    def test_none_input(self):
        assert _safe_float(None) is None

    def test_unsupported_marker(self):
        assert _safe_float("N/A") is None
        assert _safe_float("unsupported") is None
        assert _safe_float("Unknown error") is None

    def test_empty_string(self):
        assert _safe_float("") is None


class TestComputeVramPct:
    """Test VRAM percentage computation."""

    def test_normal_computation(self):
        assert _compute_vram_pct("1000", "2000") == 50.0

    def test_null_values(self):
        assert _compute_vram_pct(None, "2000") is None
        assert _compute_vram_pct("1000", None) is None

    def test_zero_total(self):
        assert _compute_vram_pct("500", "0") is None


class TestNvidiaCsvParsing:
    """Test parsing of nvidia-smi CSV output."""

    def test_single_gpu_full_metrics(self):
        """Standard nvidia-smi CSV with all fields."""
        stdout = "NVIDIA GeForce RTX 4090, 65, 87, 320.50, 16384, 24576"
        result = _parse_nvidia_csv(stdout)
        assert result["gpu_name"] == "NVIDIA GeForce RTX 4090"
        assert result["gpu_temp_c"] == 65.0
        assert result["gpu_busy_pct"] == 87.0
        assert result["gpu_power_w"] == 320.50
        # VRAM: 16384 / 24576 = 66.67%
        assert result["gpu_vram_used_pct"] == pytest.approx(66.67, rel=0.01)

    def test_multi_gpu_warning(self):
        """Multiple GPUs: only the first is stored, warning logged."""
        stdout = (
            "NVIDIA GeForce RTX 4090, 65, 87, 320.50, 16384, 24576\n"
            "NVIDIA GeForce RTX 4080, 55, 72, 250.00, 8192, 16384"
        )
        result = _parse_nvidia_csv(stdout)
        assert result["gpu_name"] == "NVIDIA GeForce RTX 4090"
        assert result["gpu_temp_c"] == 65.0

    def test_empty_output(self):
        """Empty stdout should return all None."""
        result = _parse_nvidia_csv("")
        assert all(v is None for v in result.values())

    def test_whitespace_only(self):
        """Whitespace-only should not crash."""
        result = _parse_nvidia_csv("   \n  \n  ")
        assert all(v is None for v in result.values())

    def test_partial_metrics(self):
        """Some fields may be empty / unsupported."""
        stdout = "Tesla T4, 45, , , 10240, 16384"
        result = _parse_nvidia_csv(stdout)
        assert result["gpu_name"] == "Tesla T4"
        assert result["gpu_temp_c"] == 45.0
        assert result["gpu_busy_pct"] is None  # empty
        assert result["gpu_power_w"] is None   # empty
        assert result["gpu_vram_used_pct"] == 62.5

    def test_unsupported_marker(self):
        """nvidia-smi may output [Not Supported] for some fields."""
        stdout = "Quadro P400, [Not Supported], 45, N/A, 1024, 4096"
        result = _parse_nvidia_csv(stdout)
        assert result["gpu_temp_c"] is None  # [Not Supported]
        assert result["gpu_busy_pct"] == 45.0
        assert result["gpu_power_w"] is None  # N/A
        assert result["gpu_vram_used_pct"] == 25.0

    def test_zero_vram_total(self):
        """Zero VRAM total should produce NULL percentage."""
        stdout = "Test GPU, 40, 50, 100.0, 0, 0"
        result = _parse_nvidia_csv(stdout)
        assert result["gpu_vram_used_pct"] is None
