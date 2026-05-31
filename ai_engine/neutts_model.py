import os
import re
import modal

# ---------------------------------------------------------
# 1. CẤU HÌNH MÔI TRƯỜNG MODAL (CÀI ĐẶT THƯ VIỆN & TẢI MODEL)
# ---------------------------------------------------------
image = (
    modal.Image.debian_slim(python_version="3.10")
    .apt_install("espeak-ng")
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
        "pyloudnorm"     
    )
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
    .run_commands(
        "huggingface-cli download NhatHoang2612/neutts-vietnamese-stage1 --include 'checkpoint-8000/*' --local-dir /model"
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
        # Máy Cloud sẽ chạy đoạn này nên chắc chắn có thư viện.
        import torch
        from transformers import AutoTokenizer, AutoModelForCausalLM
        from neucodec import NeuCodec
        from phonemizer.backend import EspeakBackend
        from ai_engine.text_norm import RuleBasedNormalizer
        from ai_engine.audio_norm import AudioNormalizer
        
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
        print("✅ Đã nạp xong tất cả mô hình vào VRAM đám mây!")

    @modal.method()
    def process(self, text: str, ref_audio_bytes: bytes, ref_text: str, temperature=0.75, top_k=40) -> bytes:
        # CÁC IMPORTS DÙNG CHO SUY LUẬN ĐỂ Ở ĐÂY
        import torch
        import soundfile as sf
        from librosa import load as librosa_load

        temp_ref_path = "/tmp/ref.wav"
        with open(temp_ref_path, "wb") as f:
            f.write(ref_audio_bytes)

        clean_text = self.normalizer.normalize(text)
        clean_ref_text = self.normalizer.normalize(ref_text)
        
        input_phones = ' '.join(self.phonemizer.phonemize([clean_text])[0].split())
        ref_phones = ' '.join(self.phonemizer.phonemize([clean_ref_text])[0].split())
        
        # wav, _ = librosa_load(temp_ref_path, sr=16000, mono=True)
        wav = self.audio_normalizer.normalize(ref_audio_bytes)
        
        wav_tensor = torch.from_numpy(wav).float().unsqueeze(0).unsqueeze(0)
        with torch.no_grad():
            ref_codes = self.codec.encode_code(audio_or_path=wav_tensor).squeeze(0).squeeze(0).cpu()
            
        codes_str = "".join([f"<|speech_{i}|>" for i in ref_codes.tolist()])
        combined_phones = ref_phones + " " + input_phones
        chat = f"user: Convert the text to speech:<|TEXT_PROMPT_START|>{combined_phones}<|TEXT_PROMPT_END|>\nassistant:<|SPEECH_GENERATION_START|>{codes_str}"
        
        input_ids = self.tokenizer.encode(chat, return_tensors="pt").to(self.device)
        speech_end_id = self.tokenizer.convert_tokens_to_ids("<|SPEECH_GENERATION_END|>")
        
        with torch.no_grad():
            output_tokens = self.model.generate(
                input_ids, max_length=self.max_context, eos_token_id=speech_end_id,
                do_sample=True, temperature=temperature, top_k=top_k, use_cache=True, min_new_tokens=50
            )
            
        output_str = self.tokenizer.decode(output_tokens[0, input_ids.shape[-1]:].cpu().tolist(), skip_special_tokens=False)
        
        speech_ids = [int(num) for num in re.findall(r"<\|speech_(\d+)\|>", output_str)]
        with torch.no_grad():
            codes = torch.tensor(speech_ids, dtype=torch.long)[None, None, :].to(self.device)
            recon = self.codec.decode_code(codes).cpu().numpy()
            
        temp_out_path = "/tmp/output.wav"
        sf.write(temp_out_path, recon[0, 0, :], self.sample_rate)
        
        with open(temp_out_path, "rb") as f:
            output_bytes = f.read()
            
        return output_bytes