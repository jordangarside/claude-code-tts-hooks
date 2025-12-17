"""Tests for sound generation."""

import os
from pathlib import Path

import numpy as np
import pytest

from claude_code_tts_server.core.sounds import (
    SoundManager,
    generate_chime,
    generate_drop_tone,
    save_audio,
)


class TestGenerateChime:
    """Tests for generate_chime function."""

    def test_returns_numpy_array(self):
        """Test that chime returns a numpy array."""
        chime = generate_chime()
        assert isinstance(chime, np.ndarray)

    def test_correct_dtype(self):
        """Test that chime has correct dtype."""
        chime = generate_chime()
        assert chime.dtype == np.float32

    def test_correct_sample_rate_duration(self):
        """Test that chime has expected duration."""
        sample_rate = 24000
        chime = generate_chime(sample_rate)
        # Two 0.08s notes with 0.03s gap
        expected_duration = 0.08 + 0.03 + 0.08
        expected_samples = int(sample_rate * expected_duration)
        # Allow some tolerance
        assert abs(len(chime) - expected_samples) < 100

    def test_amplitude_in_range(self):
        """Test that amplitude is in valid range."""
        chime = generate_chime()
        assert np.max(np.abs(chime)) <= 1.0


class TestGenerateDropTone:
    """Tests for generate_drop_tone function."""

    def test_returns_numpy_array(self):
        """Test that drop tone returns a numpy array."""
        tone = generate_drop_tone()
        assert isinstance(tone, np.ndarray)

    def test_correct_dtype(self):
        """Test that drop tone has correct dtype."""
        tone = generate_drop_tone()
        assert tone.dtype == np.float32

    def test_correct_duration(self):
        """Test that drop tone has expected duration."""
        sample_rate = 24000
        tone = generate_drop_tone(sample_rate)
        expected_samples = int(sample_rate * 0.15)
        assert len(tone) == expected_samples

    def test_amplitude_in_range(self):
        """Test that amplitude is in valid range."""
        tone = generate_drop_tone()
        assert np.max(np.abs(tone)) <= 1.0


class TestSaveAudio:
    """Tests for save_audio function."""

    def test_creates_file(self):
        """Test that save_audio creates a file."""
        audio = np.zeros(1000, dtype=np.float32)
        path = save_audio(audio)

        assert path.exists()
        assert path.suffix == ".wav"

        # Cleanup
        os.unlink(path)

    def test_file_is_readable(self):
        """Test that saved file can be read back."""
        import soundfile as sf

        audio = np.random.randn(24000).astype(np.float32) * 0.1
        path = save_audio(audio, 24000)

        data, sr = sf.read(path)
        assert sr == 24000
        assert len(data) == len(audio)

        # Cleanup
        os.unlink(path)

    def test_speed_requires_rubberband(self):
        """Test that speed != 1.0 requires rubberband (Python package + CLI tool)."""
        import shutil

        audio = np.random.randn(24000).astype(np.float32) * 0.1

        # Check if both pyrubberband package AND rubberband CLI are available
        try:
            import pyrubberband  # noqa: F401
            rubberband_available = shutil.which("rubberband") is not None
        except ImportError:
            rubberband_available = False

        if rubberband_available:
            # Should work if fully installed
            import soundfile as sf
            path = save_audio(audio, 24000, speed=1.5)
            data, sr = sf.read(path)
            # Sample rate unchanged (rubberband stretches audio, not sample rate)
            assert sr == 24000
            # Audio should be shorter (sped up)
            assert len(data) < len(audio)
            os.unlink(path)
        else:
            # Should raise ImportError if either component is missing
            with pytest.raises(ImportError, match="speed changes"):
                save_audio(audio, 24000, speed=1.5)


class TestSoundManager:
    """Tests for SoundManager class."""

    def test_init_sounds(self):
        """Test that init_sounds creates files."""
        manager = SoundManager()
        manager.init_sounds()

        assert manager.chime_file is not None
        assert manager.drop_file is not None
        assert manager.chime_file.exists()
        assert manager.drop_file.exists()

        manager.cleanup()

    def test_cleanup(self):
        """Test that cleanup removes files."""
        manager = SoundManager()
        manager.init_sounds()

        chime_path = manager.chime_file
        drop_path = manager.drop_file

        manager.cleanup()

        assert manager.chime_file is None
        assert manager.drop_file is None
        assert not chime_path.exists()
        assert not drop_path.exists()

    def test_cleanup_handles_missing_files(self):
        """Test that cleanup handles already-deleted files."""
        manager = SoundManager()
        manager.init_sounds()

        # Manually delete files
        os.unlink(manager.chime_file)
        os.unlink(manager.drop_file)

        # Should not raise
        manager.cleanup()
