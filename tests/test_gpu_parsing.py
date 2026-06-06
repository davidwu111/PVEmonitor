"""Tests for GPU metric parsing (rocm-smi text output)."""

import pytest
from pvemonitor.gpu import _parse_rocm_text, _parse_rocm_json, _safe_float, _compute_vram_pct


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
