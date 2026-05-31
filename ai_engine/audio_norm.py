import io
import librosa
import numpy as np
import noisereduce as nr
import pyloudnorm as pyln

class AudioNormalizer:
    def __init__(self, target_sr=16000, margin_ms=50):
        self.target_sr = target_sr
        # Tính toán số lượng mẫu (samples) cần bù đắp dựa trên mili-giây
        self.margin_samples = int(self.target_sr * margin_ms / 1000)
        print(f"🎵 Đã khởi tạo Audio Normalizer (16kHz, Mono, Khử nhiễu, LUFS, Trim + {margin_ms}ms Margin)")

    def normalize(self, audio_bytes: bytes) -> np.ndarray:
        """
        Nhận vào chuỗi Bytes của file âm thanh, trả về mảng Numpy (Waveform) đã làm sạch.
        """
        wav, sr = librosa.load(io.BytesIO(audio_bytes), sr=self.target_sr, mono=True)

        # 1. Cắt sạch khoảng lặng
        wav, _ = librosa.effects.trim(wav, top_db=30)
        
        # 2. Bù đắp lại một chút viền (margin) bằng các giá trị 0 (im lặng tuyệt đối)
        wav = np.pad(wav, (self.margin_samples, self.margin_samples), mode='constant')

        # 3. Khử nhiễu
        wav = nr.reduce_noise(y=wav, sr=self.target_sr, prop_decrease=0.6)
        
        # 4. Chuẩn hóa LUFS
        meter = pyln.Meter(self.target_sr) 
        try:
            loudness = meter.integrated_loudness(wav)
            wav = pyln.normalize.loudness(wav, loudness, -23.0)
        except Exception as e:
            print(f"⚠ Bỏ qua LUFS do audio quá ngắn hoặc lỗi: {e}")

        # 5. Chống Clip
        peak = np.abs(wav).max()
        if peak > 1.0:
            wav = wav / peak

        return wav