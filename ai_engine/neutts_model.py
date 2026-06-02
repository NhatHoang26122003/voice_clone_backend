import os
import re
import modal
import io
import difflib

# ---------------------------------------------------------
# 1. CẤU HÌNH MÔI TRƯỜNG MODAL (CÀI ĐẶT THƯ VIỆN & TẢI MODEL)
# ---------------------------------------------------------
image = (
    modal.Image.debian_slim(python_version="3.10")
    .apt_install("espeak-ng", "ffmpeg")
    .pip_install(
        "torch", 
        "transformers==4.56.1", 
        "soundfile", 
        "librosa", 
        "numpy", 
        "phonemizer", 
        "num2words", 
        "hf-transfer",
        "neucodec==0.0.4",
        "noisereduce",   
        "pyloudnorm",
        "openai-whisper"     
    )
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
    .run_commands(
        "huggingface-cli download NhatHoang2612/neutts-vietnamese-stage1 --include 'checkpoint-8000/*' --local-dir /model"
    )
    .run_commands(
        "python -c \"from neucodec import NeuCodec; NeuCodec.from_pretrained('neuphonic/neucodec')\"",
        "python -c \"import whisper; whisper.load_model('small', device='cpu')\""
    )
    .add_local_dir("./ai_engine/util", remote_path="/root/util")
    .add_local_python_source("ai_engine.text_norm")
    .add_local_python_source("ai_engine.audio_norm")
)
app = modal.App("vietnamese-voice-clone")


# ---------------------------------------------------------
# 2. KHAI BÁO CLASS CHẠY TRÊN GPU ĐÁM MÂY
# ---------------------------------------------------------
@app.cls(image=image, gpu="A10G", timeout=300) 
class VietnameseVoiceCloneEngine:
    
    @modal.enter()
    def setup(self):
        # MANG TOÀN BỘ IMPORTS VÀO ĐÂY!
        import torch
        from transformers import AutoTokenizer, AutoModelForCausalLM
        from neucodec import NeuCodec
        from phonemizer.backend import EspeakBackend
        from ai_engine.text_norm import RuleBasedNormalizer
        from ai_engine.audio_norm import AudioNormalizer
        import whisper
        
        print("🚀 Khởi tạo hệ thống AI NeuTTS-Air trên Modal GPU...")
        
        for i in range(1, 8):
            if not hasattr(torch, f'int{i}'): setattr(torch, f'int{i}', torch.int8)
            if not hasattr(torch, f'uint{i}'): setattr(torch, f'uint{i}', torch.uint8)
        if not hasattr(torch, 'float8_e5m2'): torch.float8_e5m2 = torch.float16
        if not hasattr(torch, 'float8_e4m3fn'): torch.float8_e4m3fn = torch.float16

        self.device = "cuda"
        self.sample_rate = 24000
        self.max_context = 2048
        
        self.normalizer = RuleBasedNormalizer()
        self.audio_normalizer = AudioNormalizer(target_sr=16000)
        checkpoint_path = "/model/checkpoint-8000"
        
        self.tokenizer = AutoTokenizer.from_pretrained(checkpoint_path)
        self.model = AutoModelForCausalLM.from_pretrained(
            checkpoint_path, torch_dtype=torch.bfloat16, trust_remote_code=True
        ).to(self.device)
        self.model.eval()
        
        self.codec = NeuCodec.from_pretrained("neuphonic/neucodec").to(self.device)
        self.codec.eval()
        self.phonemizer = EspeakBackend(language='vi', preserve_punctuation=True, with_stress=True)
        
        self.whisper_model = whisper.load_model("small", device=self.device)
        print("✅ Đã nạp xong tất cả mô hình (NeuTTS + Whisper) vào VRAM đám mây!")


    @modal.method()
    def extract_profile(self, ref_audio_bytes: bytes, ref_text: str) -> dict:
        import torch
        import soundfile as sf
        import whisper
        
        print(f"\n[🔍 EXTRACT] Bắt đầu quy trình kiểm tra và trích xuất giọng mẫu...")
        
        temp_ref_path = "/tmp/ref.wav"
        with open(temp_ref_path, "wb") as f:
            f.write(ref_audio_bytes)
        
        clean_ref_text = self.normalizer.normalize(ref_text).lower()
        # 1. GATEKEEPER: Whisper kiểm tra độ chính xác
        transcribe_result = self.whisper_model.transcribe(
            temp_ref_path, 
            language="vi",
            initial_prompt=clean_ref_text,
            condition_on_previous_text=False,
            temperature=0.0 
        )
        user_spoken_text = transcribe_result["text"].strip().lower()
        clean_ref_text = self.normalizer.normalize(ref_text).lower()
        
        # So sánh 2 chuỗi 
        similarity = difflib.SequenceMatcher(None, clean_ref_text, user_spoken_text).ratio()
        
        # BỔ SUNG LOG SO SÁNH WHISPER VÀ TEXT MẪU
        print(f" ├─ 📝 Kịch bản gốc (đã chuẩn hóa) : '{clean_ref_text}'")
        print(f" ├─ 🎙️ Whisper nghe được            : '{user_spoken_text}'")
        print(f" ├─ 📊 Độ khớp (Similarity)        : {similarity * 100:.2f}%")

        if similarity < 0.9:
            print(f" └─ ❌ TỪ CHỐI: Giọng đọc không đạt yêu cầu (< 98%).")
            return {"status": "rejected", "message": f"Giọng đọc không khớp. Nhận diện: '{user_spoken_text}'"}

        print(f" ├─ ✅ CHẤP NHẬN: Giọng đọc hợp lệ. Đang bắt đầu trích xuất Codes & Âm vị...")

        # 2. EXTRACT: Tạo Codes và Âm vị
        ref_phones = ' '.join(self.phonemizer.phonemize([clean_ref_text])[0].split())
        print(f" ├─ 🔤 Âm vị (Phonemes) trích xuất thành công.")
        
        wav = self.audio_normalizer.normalize(ref_audio_bytes)
        wav_tensor = torch.from_numpy(wav).float().unsqueeze(0).unsqueeze(0)
        with torch.no_grad():
            ref_codes = self.codec.encode_code(audio_or_path=wav_tensor).squeeze(0).squeeze(0).cpu()
            
        buffer = io.BytesIO()
        torch.save(ref_codes, buffer)
        codes_bytes = buffer.getvalue()
        
        print(f" └─ ✅ HOÀN TẤT: Mã hóa thành công ({len(codes_bytes)} bytes). Đang gửi về Worker.")

        return {
            "status": "ready",
            "codes_bytes": codes_bytes,
            "ref_phones": ref_phones
        }
    

    @modal.method()
    def generate_audio(self, text: str, codes_bytes: bytes, ref_phones: str, temperature=0.7, top_k=40) -> bytes:
        import torch
        import soundfile as sf
        import time
        
        print(f"\n[⚡ GENERATE] Nhận yêu cầu sinh audio siêu tốc...")
        print(f" ├─ 📜 Văn bản cần đọc: '{text}'")
        
        start_time = time.time()
        
        # 1. Đọc lại Codes từ Bytes
        buffer = io.BytesIO(codes_bytes)
        ref_codes = torch.load(buffer).to(self.device)
        print(f" ├─ 💾 Đã nạp Audio Codes từ bộ nhớ đệm (Cache).")
        
        # 2. Xử lý text đầu vào
        clean_text = self.normalizer.normalize(text)
        input_phones = ' '.join(self.phonemizer.phonemize([clean_text])[0].split())
        combined_phones = ref_phones + " " + input_phones
        
        codes_str = "".join([f"<|speech_{i}|>" for i in ref_codes.tolist()])
        chat = f"user: Convert the text to speech:<|TEXT_PROMPT_START|>{combined_phones}<|TEXT_PROMPT_END|>\nassistant:<|SPEECH_GENERATION_START|>{codes_str}"
            
        input_ids = self.tokenizer.encode(chat, return_tensors="pt").to(self.device)
        speech_end_id = self.tokenizer.convert_tokens_to_ids("<|SPEECH_GENERATION_END|>")
        
        print(f" ├─ 🧠 Đang chạy mô hình LLM để sinh Token giọng nói...")
        llm_start = time.time()
        
        with torch.no_grad():
            output_tokens = self.model.generate(
                input_ids, max_length=self.max_context, eos_token_id=speech_end_id,
                do_sample=True, temperature=temperature, top_k=top_k, use_cache=True, min_new_tokens=50
            )
            
        llm_duration = time.time() - llm_start
        print(f" ├─ ⏱️ Sinh LLM Tokens xong (Mất {llm_duration:.2f}s).")
            
        output_str = self.tokenizer.decode(output_tokens[0, input_ids.shape[-1]:].cpu().tolist(), skip_special_tokens=False)
        
        print(f" ├─ 🎵 Đang giải mã Tokens thành sóng âm thanh (NeuCodec)...")
        speech_ids = [int(num) for num in re.findall(r"<\|speech_(\d+)\|>", output_str)]
        with torch.no_grad():
            codes = torch.tensor(speech_ids, dtype=torch.long)[None, None, :].to(self.device)
            recon = self.codec.decode_code(codes).cpu().numpy()
            
        temp_out_path = "/tmp/output.wav"
        sf.write(temp_out_path, recon[0, 0, :], self.sample_rate)
        
        with open(temp_out_path, "rb") as f:
            output_bytes = f.read()
            
        total_time = time.time() - start_time
        print(f" └─ ✅ HOÀN TẤT: Tổng thời gian sinh Audio là {total_time:.2f}s. Đang trả file về Worker.")
            
        return output_bytes