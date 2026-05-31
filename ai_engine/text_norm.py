import os
import re
import json
from num2words import num2words

class RuleBasedNormalizer:
    def __init__(self):
        self.abbreviations = self._load_json("./util/abbreviation.json")
        self.currencies = self._load_json("./util/currency.json")
        self.units = self._load_json("./util/unit.json")
        self.measurements = self._load_json("./util/measurement.json")
        
        self.all_dicts = {**self.abbreviations, **self.currencies, **self.units, **self.measurements}
        self.sorted_keys = sorted(self.all_dicts.keys(), key=len, reverse=True)

    def _load_json(self, filepath):
        if os.path.exists(filepath):
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}

    def normalize_numbers(self, text):
        def replace_num(match):
            num_str = match.group()
            try:
                # 1. Đếm tổng số lượng dấu chấm và dấu phẩy bên trong cụm số
                dots_count = num_str.count('.')
                commas_count = num_str.count(',')
                total_separators = dots_count + commas_count
                
                is_decimal = False
                
                # 2. Logic phân biệt Thập phân và Hàng nghìn/Triệu
                if total_separators >= 2:
                    # Nếu có từ 2 dấu trở lên (VD: 1.000.000 hoặc 1,000,000) -> Chắc chắn là số lớn
                    is_decimal = False
                elif total_separators == 1:
                    # Nếu có đúng 1 dấu, xác định xem đó là dấu gì
                    sep = '.' if dots_count == 1 else ','
                    parts = num_str.split(sep)
                    
                    # Kiểm tra phần đuôi sau dấu phân cách
                    # Nếu có ĐÚNG 3 chữ số (VD: 100.000, 15,500) -> Dấu phân cách hàng nghìn
                    if len(parts[1]) == 3:
                        is_decimal = False
                    # Nếu KHÁC 3 chữ số (VD: 2.5, 3,14) -> Dấu thập phân
                    else:
                        is_decimal = True
                else:
                    # Không có dấu nào (VD: 2023, 50) -> Số nguyên bình thường
                    is_decimal = False
                
                # 3. Đọc thành chữ dựa trên phân loại
                if is_decimal:
                    sep = '.' if dots_count == 1 else ','
                    parts = num_str.split(sep)
                    int_part = num2words(int(parts[0]), lang='vi')
                    dec_part = ' '.join([num2words(int(digit), lang='vi') for digit in parts[1]])
                    result = f"{int_part} phẩy {dec_part}"
                else:
                    # Xóa toàn bộ dấu phân cách để đọc số lớn / số nguyên
                    clean_int = num_str.replace('.', '').replace(',', '')
                    result = num2words(int(clean_int), lang='vi')
                
                # Sửa "nghìn" thành "ngàn"
                return result.replace("nghìn", "ngàn")
            except Exception:
                return num_str
                
        # Regex này bắt toàn bộ chuỗi số chứa dấu chấm hoặc phẩy
        return re.sub(r'\b\d+([.,]\d+)*\b', replace_num, text)

    def normalize(self, text):
        if not text: return ""
        working_text = text.lower().strip()

        # --- BƯỚC 1: TÁCH CHỮ VÀ SỐ DÍNH LIỀN BẰNG LOOKAROUND ---
        # Tách chữ -> số (VD: PM2.5 -> PM 2.5, B3 -> B 3)
        working_text = re.sub(r'(?<=[a-zA-Z])(?=\d)', ' ', working_text)
        # Tách số -> chữ (VD: 48mg -> 48 mg, 50k -> 50 k, 1m7 -> 1 m 7)
        working_text = re.sub(r'(?<=\d)(?=[a-zA-Z])', ' ', working_text)

        # --- BƯỚC 2: XỬ LÝ KÝ HIỆU (% , $, °C) TRƯỚC KHI TẨY RÁC ---
        for sym, word in self.measurements.items():
            working_text = working_text.replace(sym, f" {word} ")
        for sym, word in self.currencies.items():
            if not re.match(r'^\w+$', sym):
                working_text = working_text.replace(sym, f" {word} ")

        # --- BƯỚC 3: THAY THẾ TỪ VIẾT TẮT / ĐƠN VỊ TỪ DICTIONARY ---
        for key in self.sorted_keys:
            val = self.all_dicts[key]
            if re.match(r'^\w+$', key):
                working_text = re.sub(rf'\b{key}\b', val, working_text)

        # --- BƯỚC 4: ĐỌC SỐ (Áp dụng logic thông minh ở trên) ---
        working_text = self.normalize_numbers(working_text)

        # --- BƯỚC 5: TẨY RÁC VÀ DỌN DẸP KHOẢNG TRẮNG ---
        working_text = re.sub(r'\s*([.,;?!])\s*', r'\1 ', working_text)
        working_text = re.sub(r'[^\w\s.,;?!-]', '', working_text)
        final_text = re.sub(r'\s+', ' ', working_text).strip()

        if final_text:
            final_text = final_text[0].upper() + final_text[1:]
            
        return final_text
