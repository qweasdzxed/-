"""
ComfyUI 배치 업스케일 + 얼굴 디테일러 + WebP 변환 (GUI v3)

v3 변경점:
- 창 전체 세로 스크롤 (창이 작아도 다 보임)
- 프롬프트/네거티브 각 슬롯 개별 접기 (메인 / 보조1 / 보조2)
- 각 슬롯 접힘 상태 저장
- 로그 영역은 자체 스크롤 유지

기존 기능 유지:
- ComfyUI 자동 실행
- 드래그 앤 드롭 (tkinterdnd2)
- 건너뛰기 / 덮어쓰기 / 재시도 / 폴더 열기 / 로그 저장
- 경과시간 / 남은시간
- 휠 미세조정 (슬라이더)
"""

import json, time, random, shutil, threading, os, sys, subprocess
from concurrent.futures import ThreadPoolExecutor
import urllib.request, mimetypes, io
import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext, messagebox
from pathlib import Path
from datetime import datetime
from PIL import Image

# 드래그 앤 드롭 (선택)
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    HAS_DND = True
    _BaseTk = TkinterDnD.Tk
except ImportError:
    HAS_DND = False
    _BaseTk = tk.Tk

# ─────────────────────────────────────────────
# 설정
# ─────────────────────────────────────────────
COMFYUI_HOST     = "127.0.0.1"
COMFYUI_PORT     = 8188
SUPPORTED_EXT    = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

CHECKPOINT       = "woma_00002_.safetensors"
UPSCALE_MODEL = "4x-AnimeSharp.pth"
UPSCALE_SCALE = 0.3125
CFG              = 2
OUTPUT_PREFIX    = "BATCHTEMP"

DEFAULT_PROMPT   = "detailed eyes"
DEFAULT_NEGATIVE = ""
DEFAULT_DENOISE  = 0.25
DEFAULT_STEPS    = 5
DEFAULT_BBOX_THRESHOLD = 0.5
DEFAULT_DROP_SIZE      = 10

WEBP_QUALITY     = 90
WEBP_BG_COLOR    = (255, 255, 255)
KEEP_ALPHA       = True   # 투명 배경 유지 (False면 WEBP_BG_COLOR로 채움)

MAX_RETRIES      = 2
COMFYUI_BOOT_TIMEOUT = 120  # 초

SLOT_NAMES = ["메인", "보조"]
# ─────────────────────────────────────────────

BASE_URL    = f"http://{COMFYUI_HOST}:{COMFYUI_PORT}"
CONFIG_FILE = Path(__file__).with_name("config.json")


# ── Config ───────────────────────────────────

def load_config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}

def save_config(data: dict):
    try:
        CONFIG_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


# ── 유틸 ─────────────────────────────────────

def fmt_size(b: int) -> str:
    if b < 1024:        return f"{b}B"
    if b < 1024 ** 2:   return f"{b/1024:.1f}KB"
    return f"{b/1024**2:.1f}MB"

def fmt_time(seconds: float) -> str:
    if seconds < 0 or seconds > 86400:
        return "--:--"
    s = int(seconds)
    if s < 60:
        return f"{s}초"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m:02d}:{s:02d}"
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}"

def open_folder(path: Path):
    try:
        if sys.platform == "win32":
            os.startfile(str(path))
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)])
        else:
            subprocess.run(["xdg-open", str(path)])
    except Exception:
        pass


# ── 투명도 처리 ──────────────────────────────
# ComfyUI의 LoadImage는 알파를 버리고 RGB만 쓰기 때문에, 투명 영역에 숨어 있던
# 쓰레기 색(대개 검정)이 그대로 업스케일/디테일러를 거쳐 나온다.
# → 업로드 전에 투명 영역을 주변 색으로 번지게 채운 불투명 이미지를 보내고,
#   결과물에 원본 알파를 리사이즈해서 다시 붙인다.

def load_alpha_image(path: Path):
    """실제로 투명한 픽셀이 있으면 RGBA 이미지를, 아니면 None을 돌려준다."""
    with Image.open(path) as im:
        if im.mode not in ("RGBA", "LA", "PA") and "transparency" not in im.info:
            return None
        rgba = im.convert("RGBA")
    if rgba.getchannel("A").getextrema()[0] == 255:
        return None
    return rgba

def bleed_fill(rgba: Image.Image) -> Image.Image:
    """투명 영역을 가장자리 색으로 번지게 채워 불투명 RGB로 만든다 (외곽 헤일로 방지)."""
    w, h = rgba.size
    canvas = Image.new("RGBA", (w, h), (128, 128, 128, 255))
    f = 1
    while max(w, h) // (f * 2) >= 1:
        f *= 2
    while f >= 2:
        # Pillow는 RGBA 리사이즈를 premultiplied로 처리하므로 투명 픽셀 색이 섞이지 않는다
        small = rgba.resize((max(1, w // f), max(1, h // f)), Image.BOX)
        canvas.alpha_composite(small.resize((w, h), Image.BILINEAR))
        f //= 2
    canvas.alpha_composite(rgba)
    return canvas.convert("RGB")

def restore_alpha(dest_png: Path, original: Path) -> bool:
    """원본에 투명도가 있었으면 결과 PNG 크기에 맞춰 알파를 다시 붙인다."""
    rgba = load_alpha_image(original)
    if rgba is None:
        return False
    with Image.open(dest_png) as out:
        out = out.convert("RGB")
    alpha = rgba.getchannel("A").resize(out.size, Image.LANCZOS)
    out.putalpha(alpha)
    out.save(dest_png, "PNG")
    return True


# ── WebP 변환 ────────────────────────────────

def convert_to_webp(src: Path, dest: Path, quality: int = WEBP_QUALITY,
                    bg: tuple = WEBP_BG_COLOR) -> dict:
    size_before = src.stat().st_size
    try:
        img = Image.open(src)
        if KEEP_ALPHA and (img.mode in ("RGBA", "LA", "PA")
                           or "transparency" in img.info):
            img = img.convert("RGBA")
        elif img.mode in ("RGBA", "LA", "P"):
            background = Image.new("RGB", img.size, bg)
            if img.mode == "P":
                img = img.convert("RGBA")
            if img.mode in ("RGBA", "LA"):
                background.paste(img, mask=img.split()[-1])
            else:
                background.paste(img)
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")
        dest.parent.mkdir(parents=True, exist_ok=True)
        img.save(dest, "WEBP", quality=quality, method=6)
        size_after = dest.stat().st_size
        ratio = (1 - size_after / size_before) * 100 if size_before else 0
        return {"ok": True, "before": size_before, "after": size_after, "ratio": ratio}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── ComfyUI 경로 자동 탐지 ───────────────────

def _is_comfyui(path: Path) -> bool:
    return (path.is_dir()
            and (path / "main.py").exists()
            and (path / "output").is_dir()
            and (path / "input").is_dir())

def _find_via_wmi() -> Path | None:
    try:
        result = subprocess.run(
            ["wmic", "process", "where",
             "name='python.exe' or name='pythonw.exe'",
             "get", "CommandLine", "/format:list"],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            if "main.py" in line and "comfyui" in line.lower():
                for part in line.split():
                    p = Path(part).parent
                    if _is_comfyui(p):
                        return p
    except Exception:
        pass
    return None

def find_comfyui_root() -> Path | None:
    candidate = _find_via_wmi()
    if candidate:
        return candidate
    search_roots = []
    for letter in "CDEFGHIJ":
        drive = Path(f"{letter}:\\")
        if drive.exists():
            search_roots.append(drive)
    home = Path.home()
    candidates = []
    for root in search_roots:
        candidates += [
            root / "ComfyUI", root / "comfyui",
            root / "AI" / "ComfyUI", root / "stable-diffusion" / "ComfyUI",
            root / "SD" / "ComfyUI", root / "tools" / "ComfyUI",
        ]
    candidates += [
        home / "ComfyUI", home / "Desktop" / "ComfyUI",
        home / "Documents" / "ComfyUI", home / "Downloads" / "ComfyUI",
    ]
    for p in candidates:
        if _is_comfyui(p):
            return p
    for root in search_roots:
        try:
            for sub in root.iterdir():
                if not sub.is_dir(): continue
                if _is_comfyui(sub): return sub
                try:
                    for sub2 in sub.iterdir():
                        if sub2.is_dir() and _is_comfyui(sub2):
                            return sub2
                except PermissionError:
                    pass
        except PermissionError:
            pass
    return None


# ── ComfyUI 자동 실행 ────────────────────────

def start_comfyui(comfyui_root: Path):
    flags = subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0
    for bat_dir in (comfyui_root.parent, comfyui_root):
        for name in ("run_nvidia_gpu.bat", "run_cpu.bat"):
            bat = bat_dir / name
            if bat.exists():
                return subprocess.Popen([str(bat)], cwd=str(bat.parent), creationflags=flags)
    embed = comfyui_root.parent / "python_embeded" / "python.exe"
    if embed.exists():
        return subprocess.Popen(
            [str(embed), "-s", "main.py"],
            cwd=str(comfyui_root), creationflags=flags
        )
    if (comfyui_root / "main.py").exists():
        return subprocess.Popen(
            [sys.executable, "main.py"],
            cwd=str(comfyui_root), creationflags=flags
        )
    return None

def wait_for_comfyui(timeout: int = COMFYUI_BOOT_TIMEOUT) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        try:
            api_get("system_stats")
            return True
        except Exception:
            time.sleep(2)
    return False


# ── ComfyUI API ──────────────────────────────

def api_post(endpoint, payload):
    data = json.dumps(payload).encode()
    req  = urllib.request.Request(
        f"{BASE_URL}/{endpoint}", data=data,
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())

def api_get(endpoint):
    with urllib.request.urlopen(f"{BASE_URL}/{endpoint}") as r:
        return json.loads(r.read())

def upload_image(file_path: Path) -> str:
    rgba = load_alpha_image(file_path)
    if rgba is not None:
        # 투명 이미지는 투명 영역을 채운 불투명 PNG로 바꿔서 올린다
        buf = io.BytesIO()
        bleed_fill(rgba).save(buf, "PNG")
        data, name, mime = buf.getvalue(), f"{file_path.stem}.png", "image/png"
    else:
        with open(file_path, "rb") as f:
            data = f.read()
        name = file_path.name
        mime = mimetypes.guess_type(str(file_path))[0] or "image/png"
    bound = "----B" + str(random.randint(100000, 999999))
    body = (
        f"--{bound}\r\nContent-Disposition: form-data; name=\"image\"; "
        f"filename=\"{name}\"\r\nContent-Type: {mime}\r\n\r\n"
    ).encode() + data + f"\r\n--{bound}--\r\n".encode()
    req = urllib.request.Request(
        f"{BASE_URL}/upload/image", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={bound}"}
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read()).get("name", name)

def build_workflow(filename, prompt, negative, denoise, steps,
                   bbox_threshold=0.5, drop_size=10):
    seed = random.randint(0, 2**32 - 1)
    return {
        "1": {"inputs": {"image": filename}, "class_type": "LoadImage"},
        "2": {"inputs": {"model_name": UPSCALE_MODEL}, "class_type": "UpscaleModelLoader"},
        "3": {"inputs": {"upscale_model": ["2",0], "image": ["1",0]}, "class_type": "ImageUpscaleWithModel"},
        "4": {"inputs": {"upscale_method": "nearest-exact", "scale_by": UPSCALE_SCALE, "image": ["3",0]}, "class_type": "ImageScaleBy"},
        "5": {"inputs": {"filename_prefix": OUTPUT_PREFIX, "images": ["6",0]}, "class_type": "SaveImage"},
        "6": {"inputs": {
                "guide_size": 512, "guide_size_for": True, "max_size": 1024,
                "seed": seed, "steps": int(steps), "cfg": CFG,
                "sampler_name": "euler", "scheduler": "simple",
                "denoise": float(denoise), "feather": 5, "noise_mask": True,
                "force_inpaint": True, "bbox_threshold": float(bbox_threshold), "bbox_dilation": 10,
                "bbox_crop_factor": 3, "sam_detection_hint": "center-1",
                "sam_dilation": 0, "sam_threshold": 0.93, "sam_bbox_expansion": 0,
                "sam_mask_hint_threshold": 0.7, "sam_mask_hint_use_negative": "False",
                "drop_size": int(drop_size), "wildcard": "", "cycle": 1,
                "inpaint_model": False, "noise_mask_feather": 20,
                "tiled_encode": False, "tiled_decode": False,
                "image": ["4",0], "model": ["8",0], "clip": ["8",1],
                "vae": ["7",2], "positive": ["9",0], "negative": ["10",0],
                "bbox_detector": ["11",0], "sam_model_opt": ["12",0],
                "segm_detector_opt": ["11",1]
            }, "class_type": "FaceDetailer"},
        "7": {"inputs": {"ckpt_name": CHECKPOINT}, "class_type": "CheckpointLoaderSimple"},
        "8": {"inputs": {"PowerLoraLoaderHeaderWidget": {"type":"PowerLoraLoaderHeaderWidget"},
                         "➕ Add Lora": "", "model": ["7",0], "clip": ["7",1]},
              "class_type": "Power Lora Loader (rgthree)"},
        "9":  {"inputs": {"text": prompt,   "clip": ["7",1]}, "class_type": "CLIPTextEncode"},
        "10": {"inputs": {"text": negative, "clip": ["7",1]}, "class_type": "CLIPTextEncode"},
        "11": {"inputs": {"model_name": "bbox/Eyes.pt"}, "class_type": "UltralyticsDetectorProvider"},
        "12": {"inputs": {"model_name": "sam_vit_b_01ec64.pth", "device_mode": "AUTO"}, "class_type": "SAMLoader"},
    }

def wait_for_output(prompt_id, comfyui_output: Path):
    while True:
        hist = api_get(f"history/{prompt_id}")
        if prompt_id in hist:
            info = hist[prompt_id]
            if info.get("status", {}).get("completed"):
                files = []
                for node_out in info.get("outputs", {}).values():
                    for img in node_out.get("images", []):
                        sub = img.get("subfolder", "")
                        p = comfyui_output / sub / img["filename"] if sub else comfyui_output / img["filename"]
                        files.append(p)
                return files
            if info.get("status", {}).get("status_str") == "error":
                return []
        time.sleep(2)

def move_to_output(files: list, original: Path, out_dir: Path) -> Path | None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for src in files:
        if Path(src).exists():
            dest = out_dir / f"{original.stem}.png"
            if dest.exists():
                try: dest.unlink()
                except Exception: pass
            shutil.move(str(src), str(dest))
            return dest
    return None


# ── 색상 (Zinc 다크/화이트) ──────────────────

BG       = "#09090b"
PANEL    = "#18181b"
CARD     = "#27272a"
BORDER   = "#3f3f46"
TEXT     = "#fafafa"
DIM      = "#a1a1aa"
MUTED    = "#71717a"
WHITE    = "#ffffff"
SUCCESS  = "#22c55e"
WARNING  = "#f59e0b"
DANGER   = "#ef4444"
INFO     = "#60a5fa"
TEAL     = "#2dd4bf"


# ── GUI ─────────────────────────────────────

class App(_BaseTk):
    def __init__(self):
        super().__init__()
        self.title("ComfyUI Batch  —  Upscale · Detailer · WebP")
        self.geometry("660x780")
        self.minsize(660, 420)
        self.configure(bg=BG)

        self._stop        = False
        self._comfyui_out = None
        self._cfg         = load_config()
        self._log_file    = None
        self._start_time  = 0
        self._processed   = 0

        # 슬롯별 컨테이너/화살표 저장 (접기용)
        self.prompt_texts      = []
        self.prompt_slot_frms  = []
        self.prompt_slot_arrs  = []
        self.negative_texts    = []
        self.negative_slot_frms = []
        self.negative_slot_arrs = []

        self._build_ui()
        self._restore_state()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        saved = self._cfg.get("comfyui_root")
        if saved and _is_comfyui(Path(saved)):
            self._comfyui_out = Path(saved) / "output"
            self.comfyui_lbl.config(text=saved, fg=SUCCESS)
        else:
            threading.Thread(target=self._detect_comfyui, daemon=True).start()

    # ── UI 빌드 (스크롤 컨테이너) ─────────────

    def _build_ui(self):
        # ttk 스타일
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TScale", background=BG, troughcolor=CARD, borderwidth=0)
        style.configure("Horizontal.TProgressbar",
                        background=WHITE, troughcolor=CARD,
                        borderwidth=0, lightcolor=WHITE, darkcolor=WHITE)
        style.configure("Vertical.TScrollbar",
                        background=CARD, troughcolor=BG,
                        borderwidth=0, arrowcolor=DIM)

        # 스크롤 컨테이너
        outer = tk.Frame(self, bg=BG)
        outer.pack(fill="both", expand=True)
        self._canvas = tk.Canvas(outer, bg=BG, highlightthickness=0, bd=0)
        vsb = ttk.Scrollbar(outer, orient="vertical",
                            command=self._canvas.yview,
                            style="Vertical.TScrollbar")
        self._canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)

        content = tk.Frame(self._canvas, bg=BG)
        self._ui_parent = content
        self._win_id = self._canvas.create_window((0, 0), window=content, anchor="nw")

        content.bind("<Configure>",
            lambda e: self._canvas.configure(scrollregion=self._canvas.bbox("all")))
        self._canvas.bind("<Configure>",
            lambda e: self._canvas.itemconfig(self._win_id, width=e.width))

        # 휠 스크롤: 캔버스에 바인딩 (로그 위젯은 나중에 별도 처리)
        self._canvas.bind_all("<MouseWheel>", self._on_canvas_wheel)

        # === 이제 content(self._ui_parent)를 부모로 모든 섹션 구성 ===
        p = self._ui_parent

        # 상단: ComfyUI 경로
        head = tk.Frame(p, bg=BG)
        head.pack(fill="x", padx=14, pady=(14, 6))
        tk.Label(head, text="⚙", bg=BG, fg=DIM, font=("Segoe UI", 11)).pack(side="left")
        tk.Label(head, text="ComfyUI", bg=BG, fg=DIM,
                 font=("Segoe UI", 9)).pack(side="left", padx=(4, 10))
        self.comfyui_lbl = tk.Label(head, text="탐색 중...", bg=BG, fg=WARNING,
                                     font=("Segoe UI", 9), anchor="w")
        self.comfyui_lbl.pack(side="left", fill="x", expand=True)
        tk.Button(head, text="재탐색", bg=CARD, fg=TEXT, relief="flat",
                  activebackground=BORDER, activeforeground=TEXT,
                  font=("Segoe UI", 8), cursor="hand2", bd=0, padx=10, pady=2,
                  command=lambda: threading.Thread(
                      target=self._detect_comfyui, daemon=True).start()
                  ).pack(side="right")

        self._divider()

        self._section_title("📁  폴더")
        self._folder_row("입력", "in_var", "in")
        self._folder_row("PNG",  "out_var", "out")
        self._webp_row()

        self._divider()

        self._section_title("🎛  설정")
        self._settings_section()

        self._divider()

        self._section_title("⚡  옵션")
        self._options_section()

        self._divider()

        self._progress_section()

        # 로그
        self.log = scrolledtext.ScrolledText(
            p, width=66, height=11,
            bg=PANEL, fg=TEXT, font=("Consolas", 9),
            relief="flat", state="disabled", bd=0,
            insertbackground=TEXT
        )
        self.log.pack(padx=14, pady=(8, 4), fill="x")
        self.log.tag_config("ok",   foreground=SUCCESS)
        self.log.tag_config("err",  foreground=DANGER)
        self.log.tag_config("dim",  foreground=MUTED)
        self.log.tag_config("warn", foreground=WARNING)
        self.log.tag_config("webp", foreground=TEAL)
        self.log.tag_config("info", foreground=INFO)

        # 로그 영역 위에서는 로그 자체 스크롤 (캔버스로 전파 차단)
        def _log_wheel(e):
            self.log.yview_scroll(int(-1 * (e.delta / 120)), "units")
            return "break"
        self.log.bind("<MouseWheel>", _log_wheel)

        # 버튼
        bf = tk.Frame(p, bg=BG)
        bf.pack(pady=(6, 14))
        self.start_btn = tk.Button(bf, text="▶  Start", width=16,
            bg=WHITE, fg=BG, font=("Segoe UI", 11, "bold"),
            relief="flat", cursor="hand2", bd=0,
            activebackground=DIM, activeforeground=BG,
            command=self._start)
        self.start_btn.pack(side="left", padx=6, ipady=4)
        self.stop_btn = tk.Button(bf, text="■  Stop", width=16,
            bg=CARD, fg=TEXT, font=("Segoe UI", 11, "bold"),
            relief="flat", cursor="hand2", bd=0,
            activebackground=BORDER, activeforeground=TEXT,
            state="disabled", command=self._stop_now)
        self.stop_btn.pack(side="left", padx=6, ipady=4)

    # ── 공통 헬퍼 ────────────────────────────

    def _on_canvas_wheel(self, e):
        # 로그 위젯 위에선 아무것도 안 함 (로그 자체 바인딩이 처리)
        try:
            w = self.winfo_containing(e.x_root, e.y_root)
        except Exception:
            w = None
        # 로그 위젯이나 그 하위면 캔버스 스크롤 안 함
        if w is not None:
            cur = w
            while cur is not None:
                if cur is self.log:
                    return
                try:
                    cur = cur.master
                except Exception:
                    break
        self._canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")

    def _section_title(self, text):
        tk.Label(self._ui_parent, text=text, bg=BG, fg=DIM,
                 font=("Segoe UI", 9)).pack(anchor="w", padx=14, pady=(6, 2))

    def _divider(self):
        tk.Frame(self._ui_parent, bg=BORDER, height=1).pack(
            fill="x", padx=14, pady=6)

    def _bind_wheel(self, widget, var, step, lo, hi, label, fmt, is_int=False):
        def on_wheel(e):
            d = step if e.delta > 0 else -step
            val = var.get() + d
            if is_int:
                val = int(round(val))
            val = max(lo, min(hi, round(val, 3)))
            var.set(val)
            label.config(text=fmt(val))
            return "break"
        widget.bind("<MouseWheel>", on_wheel)

    # ── 폴더 행 ──────────────────────────────

    def _folder_row(self, label, var_name, kind):
        frm = tk.Frame(self._ui_parent, bg=BG)
        frm.pack(fill="x", padx=14, pady=2)
        tk.Label(frm, text=label, bg=BG, fg=DIM,
                 font=("Segoe UI", 9), width=7, anchor="w").pack(side="left")
        var = tk.StringVar()
        setattr(self, var_name, var)
        ent = tk.Entry(frm, textvariable=var,
                 bg=CARD, fg=TEXT, insertbackground=TEXT,
                 relief="flat", font=("Segoe UI", 9), bd=0)
        ent.pack(side="left", padx=8, ipady=5, fill="x", expand=True)
        self._register_dnd(ent, kind)
        tk.Button(frm, text="…", bg=CARD, fg=TEXT, relief="flat",
                  font=("Segoe UI", 10, "bold"), width=3, bd=0,
                  activebackground=BORDER, activeforeground=TEXT,
                  cursor="hand2",
                  command=lambda k=kind: self._pick(k)).pack(side="left", ipady=3)

    def _webp_row(self):
        frm = tk.Frame(self._ui_parent, bg=BG)
        frm.pack(fill="x", padx=14, pady=2)
        self.webp_enabled = tk.BooleanVar(value=self._cfg.get("webp_enabled", True))
        tk.Checkbutton(frm, text="WebP", variable=self.webp_enabled,
                       bg=BG, fg=TEAL, selectcolor=CARD, activebackground=BG,
                       activeforeground=TEAL, font=("Segoe UI", 9),
                       width=6, anchor="w", bd=0, highlightthickness=0,
                       command=self._on_webp_toggle).pack(side="left")
        self.webp_var = tk.StringVar()
        self.webp_entry = tk.Entry(frm, textvariable=self.webp_var,
                 bg=CARD, fg=TEAL, insertbackground=TEAL,
                 relief="flat", font=("Segoe UI", 9), bd=0)
        self.webp_entry.pack(side="left", padx=8, ipady=5, fill="x", expand=True)
        self._register_dnd(self.webp_entry, "webp")
        self.webp_btn = tk.Button(frm, text="…", bg=CARD, fg=TEAL, relief="flat",
                  font=("Segoe UI", 10, "bold"), width=3, bd=0,
                  activebackground=BORDER, activeforeground=TEAL,
                  cursor="hand2",
                  command=lambda: self._pick("webp"))
        self.webp_btn.pack(side="left", ipady=3)
        self._on_webp_toggle()

    def _on_webp_toggle(self):
        state = "normal" if self.webp_enabled.get() else "disabled"
        self.webp_entry.config(state=state)
        self.webp_btn.config(state=state)
        self._cfg["webp_enabled"] = self.webp_enabled.get()
        save_config(self._cfg)

    # ── 그룹 토글 / 슬롯 토글 ────────────────

    def _toggle_group(self, container, arrow, key):
        if container.winfo_ismapped():
            container.pack_forget()
            arrow.config(text="▶")
            self._cfg[f"{key}_expanded"] = False
        else:
            container.pack(fill="x", pady=(2, 0))
            arrow.config(text="▼")
            self._cfg[f"{key}_expanded"] = True
        save_config(self._cfg)

    def _toggle_slot(self, body, arrow, key, idx):
        if body.winfo_ismapped():
            body.pack_forget()
            arrow.config(text="▶")
            self._cfg[f"{key}_slot_expanded_{idx}"] = False
        else:
            body.pack(fill="x", pady=(2, 0))
            arrow.config(text="▼")
            self._cfg[f"{key}_slot_expanded_{idx}"] = True
        save_config(self._cfg)

    def _build_slots(self, parent, key, default_values):
        """
        프롬프트/네거티브 각각 2슬롯 생성 (메인, 보조).
        parent: 부모 프레임
        key: "prompt" or "negative"
        default_values: 2개 이상의 리스트 (앞 2개만 사용)
        """
        texts, slot_frms, slot_arrs = [], [], []

        for i in range(2):
            # 각 슬롯 컨테이너 (헤더 + 본문)
            slot = tk.Frame(parent, bg=BG)
            slot.pack(fill="x", pady=1)

            # 헤더 (화살표 + 이름 + 클릭 영역)
            hdr = tk.Frame(slot, bg=BG)
            hdr.pack(fill="x")
            arr = tk.Label(hdr, text="▼", bg=BG, fg=MUTED,
                          font=("Segoe UI", 8), cursor="hand2", width=2)
            arr.pack(side="left")
            name_lbl = tk.Label(hdr, text=SLOT_NAMES[i],
                          bg=BG, fg=DIM, font=("Segoe UI", 8),
                          cursor="hand2")
            name_lbl.pack(side="left", padx=(2, 0))

            # 본문 (텍스트 박스) - 메인은 10줄, 보조는 5줄
            slot_height = 10 if i == 0 else 5
            body = tk.Frame(slot, bg=BG)
            body.pack(fill="x", pady=(2, 0))
            t = tk.Text(body, height=slot_height, bg=CARD, fg=TEXT,
                        insertbackground=TEXT,
                        relief="flat", font=("Segoe UI", 9), bd=0,
                        wrap="word", padx=6, pady=4)
            t.pack(fill="x", padx=(18, 0))
            t.insert("1.0", default_values[i] if i < len(default_values) else "")

            # 슬롯별 접힘 복원 (기본: 메인만 펼침, 보조는 접힘)
            default_expanded = (i == 0)
            expanded = self._cfg.get(
                f"{key}_slot_expanded_{i}", default_expanded)
            if not expanded:
                body.pack_forget()
                arr.config(text="▶")

            # 클릭 바인딩
            for w in (arr, name_lbl):
                w.bind("<Button-1>",
                    lambda e, b=body, a=arr, k=key, idx=i:
                        self._toggle_slot(b, a, k, idx))

            texts.append(t)
            slot_frms.append(body)
            slot_arrs.append(arr)

        return texts, slot_frms, slot_arrs

    # ── 설정 섹션 ────────────────────────────

    def _settings_section(self):
        p = self._ui_parent

        # ═══ 프롬프트 (그룹 접기 + 슬롯별 접기) ═══
        psec = tk.Frame(p, bg=BG)
        psec.pack(fill="x", padx=14, pady=(4, 2))
        ph = tk.Frame(psec, bg=BG)
        ph.pack(fill="x")
        self.prompt_arrow = tk.Label(ph, text="▼", bg=BG, fg=DIM,
                font=("Segoe UI", 9), cursor="hand2")
        self.prompt_arrow.pack(side="left", padx=(0, 4))
        lbl_p = tk.Label(ph, text="프롬프트", bg=BG, fg=TEXT,
                font=("Segoe UI", 9, "bold"), cursor="hand2")
        lbl_p.pack(side="left")
        self.prompt_container = tk.Frame(psec, bg=BG)
        self.prompt_container.pack(fill="x", pady=(2, 0))

        pslots = self._cfg.get("prompt_slots") or [
            self._cfg.get("prompt", DEFAULT_PROMPT), ""]
        while len(pslots) < 2: pslots.append("")
        (self.prompt_texts,
         self.prompt_slot_frms,
         self.prompt_slot_arrs) = self._build_slots(
            self.prompt_container, "prompt", pslots)

        for w in (self.prompt_arrow, lbl_p):
            w.bind("<Button-1>", lambda e: self._toggle_group(
                self.prompt_container, self.prompt_arrow, "prompt"))
        if not self._cfg.get("prompt_expanded", True):
            self.prompt_container.pack_forget()
            self.prompt_arrow.config(text="▶")

        # ═══ 네거티브 ═══
        nsec = tk.Frame(p, bg=BG)
        nsec.pack(fill="x", padx=14, pady=(6, 2))
        nh = tk.Frame(nsec, bg=BG)
        nh.pack(fill="x")
        self.negative_arrow = tk.Label(nh, text="▼", bg=BG, fg=DIM,
                font=("Segoe UI", 9), cursor="hand2")
        self.negative_arrow.pack(side="left", padx=(0, 4))
        lbl_n = tk.Label(nh, text="네거티브", bg=BG, fg=TEXT,
                font=("Segoe UI", 9, "bold"), cursor="hand2")
        lbl_n.pack(side="left")
        self.negative_container = tk.Frame(nsec, bg=BG)
        self.negative_container.pack(fill="x", pady=(2, 0))

        nslots = self._cfg.get("negative_slots") or [
            self._cfg.get("negative", DEFAULT_NEGATIVE), ""]
        while len(nslots) < 2: nslots.append("")
        (self.negative_texts,
         self.negative_slot_frms,
         self.negative_slot_arrs) = self._build_slots(
            self.negative_container, "negative", nslots)

        for w in (self.negative_arrow, lbl_n):
            w.bind("<Button-1>", lambda e: self._toggle_group(
                self.negative_container, self.negative_arrow, "negative"))
        if not self._cfg.get("negative_expanded", True):
            self.negative_container.pack_forget()
            self.negative_arrow.config(text="▶")

        # ═══ 순서 스왑 체크박스 ═══
        swap_frm = tk.Frame(p, bg=BG)
        swap_frm.pack(fill="x", padx=14, pady=(6, 2))
        self.swap_order = tk.BooleanVar(
            value=self._cfg.get("swap_order", False))
        tk.Checkbutton(swap_frm,
            text="보조를 앞에 배치 (보조, 메인)",
            variable=self.swap_order,
            bg=BG, fg=DIM, selectcolor=CARD,
            activebackground=BG, activeforeground=TEXT,
            font=("Segoe UI", 9), bd=0, highlightthickness=0,
            anchor="w",
            command=self._save_swap).pack(anchor="w")

        # ─── 슬라이더들 ───
        self._slider_row("Denoise", "denoise_var", "denoise_lbl",
                         0.0, 1.0, DEFAULT_DENOISE, "denoise",
                         is_float=True, step=0.01, fmt=lambda v: f"{v:.2f}")
        self._slider_row("Steps", "steps_var", "steps_lbl",
                         1, 30, DEFAULT_STEPS, "steps",
                         is_float=False, step=1, fmt=lambda v: str(v))
        self._slider_row("감지 민감도", "bbox_var", "bbox_lbl",
                         0.05, 1.0, DEFAULT_BBOX_THRESHOLD, "bbox_threshold",
                         is_float=True, step=0.01, fmt=lambda v: f"{v:.2f}")
        self._slider_row("최소 크기", "drop_var", "drop_lbl",
                         1, 50, DEFAULT_DROP_SIZE, "drop_size",
                         is_float=False, step=1, fmt=lambda v: str(v))

    def _slider_row(self, label, var_attr, lbl_attr,
                    lo, hi, default, cfg_key,
                    is_float=True, step=0.01, fmt=None):
        frm = tk.Frame(self._ui_parent, bg=BG)
        frm.pack(fill="x", padx=14, pady=2)
        tk.Label(frm, text=label, bg=BG, fg=DIM,
                 font=("Segoe UI", 9), width=10, anchor="w").pack(side="left")
        if is_float:
            var = tk.DoubleVar(value=self._cfg.get(cfg_key, default))
        else:
            var = tk.IntVar(value=self._cfg.get(cfg_key, default))
        setattr(self, var_attr, var)
        lbl = tk.Label(frm, text=fmt(var.get()),
            bg=BG, fg=TEXT, font=("Consolas", 10, "bold"), width=5)
        lbl.pack(side="right", padx=(6, 0))
        setattr(self, lbl_attr, lbl)

        def on_change(v):
            if is_float:
                lbl.config(text=fmt(float(v)))
            else:
                lbl.config(text=fmt(int(float(v))))

        sc = ttk.Scale(frm, from_=lo, to=hi, variable=var,
                  orient="horizontal", length=380,
                  command=on_change)
        sc.pack(side="left", padx=8, fill="x", expand=True)
        self._bind_wheel(sc, var, step, lo, hi, lbl, fmt, is_int=not is_float)

    # ── 옵션 섹션 ────────────────────────────

    def _options_section(self):
        frm = tk.Frame(self._ui_parent, bg=BG)
        frm.pack(fill="x", padx=14, pady=2)

        self.skip_processed = tk.BooleanVar(value=self._cfg.get("skip_processed", True))
        self.retry_on_fail  = tk.BooleanVar(value=self._cfg.get("retry_on_fail", True))
        self.auto_open      = tk.BooleanVar(value=self._cfg.get("auto_open", True))
        self.save_log       = tk.BooleanVar(value=self._cfg.get("save_log", True))

        def mk_cb(parent, text, var):
            return tk.Checkbutton(parent, text=text, variable=var,
                bg=BG, fg=TEXT, selectcolor=CARD,
                activebackground=BG, activeforeground=TEXT,
                font=("Segoe UI", 9), bd=0, highlightthickness=0,
                anchor="w",
                command=self._save_options)

        col1 = tk.Frame(frm, bg=BG)
        col1.pack(side="left", fill="x", expand=True)
        mk_cb(col1, "이미 처리된 파일 건너뛰기", self.skip_processed).pack(anchor="w")
        mk_cb(col1, "실패 시 자동 재시도",        self.retry_on_fail ).pack(anchor="w")

        col2 = tk.Frame(frm, bg=BG)
        col2.pack(side="left", fill="x", expand=True)
        mk_cb(col2, "완료 후 폴더 열기",          self.auto_open     ).pack(anchor="w")
        mk_cb(col2, "로그 파일 저장",             self.save_log      ).pack(anchor="w")

    def _save_options(self):
        self._cfg["skip_processed"] = self.skip_processed.get()
        self._cfg["retry_on_fail"]  = self.retry_on_fail.get()
        self._cfg["auto_open"]      = self.auto_open.get()
        self._cfg["save_log"]       = self.save_log.get()
        save_config(self._cfg)

    def _save_swap(self):
        self._cfg["swap_order"] = self.swap_order.get()
        save_config(self._cfg)

    # ── 진행 섹션 ────────────────────────────

    def _progress_section(self):
        p = self._ui_parent
        frm = tk.Frame(p, bg=BG)
        frm.pack(fill="x", padx=14, pady=(4, 2))
        tk.Label(frm, text="업스케일", bg=BG, fg=DIM,
                 font=("Segoe UI", 8), width=8, anchor="w").pack(side="left")
        self.progress1 = ttk.Progressbar(frm, length=340, mode="determinate")
        self.progress1.pack(side="left", fill="x", expand=True)
        self.count_lbl = tk.Label(frm, text="0 / 0", bg=BG, fg=TEXT,
                                   font=("Consolas", 9), width=10)
        self.count_lbl.pack(side="left", padx=6)

        frm2 = tk.Frame(p, bg=BG)
        frm2.pack(fill="x", padx=14, pady=2)
        tk.Label(frm2, text="WebP", bg=BG, fg=TEAL,
                 font=("Segoe UI", 8), width=8, anchor="w").pack(side="left")
        self.progress2 = ttk.Progressbar(frm2, length=340, mode="determinate")
        self.progress2.pack(side="left", fill="x", expand=True)
        self.webp_lbl = tk.Label(frm2, text="0 / 0", bg=BG, fg=TEAL,
                                  font=("Consolas", 9), width=10)
        self.webp_lbl.pack(side="left", padx=6)

        frm3 = tk.Frame(p, bg=BG)
        frm3.pack(fill="x", padx=14, pady=(4, 2))
        self.time_lbl = tk.Label(frm3, text="경과 --:--   남은시간 --:--",
                bg=BG, fg=MUTED, font=("Consolas", 9))
        self.time_lbl.pack(anchor="w")

    # ── 드래그 앤 드롭 ───────────────────────

    def _register_dnd(self, widget, kind):
        if not HAS_DND:
            return
        widget.drop_target_register(DND_FILES)
        widget.dnd_bind("<<Drop>>", lambda e, k=kind: self._on_drop(e, k))

    def _on_drop(self, event, kind):
        path = event.data.strip()
        if path.startswith("{") and path.endswith("}"):
            path = path[1:-1]
        path = path.split("} {")[0].strip("{}")
        p = Path(path)
        if p.is_file():
            p = p.parent
        if p.is_dir():
            self._apply_folder(str(p), kind)

    # ── 폴더 선택 ────────────────────────────

    def _pick(self, kind):
        d = filedialog.askdirectory()
        if d:
            self._apply_folder(d, kind)

    def _apply_folder(self, d, kind):
        p = Path(d)
        if kind == "in":
            self.in_var.set(d)
            self._cfg["input_folder"] = d
            auto_out = str(p.parent / f"{p.name}_(아웃풋)")
            self.out_var.set(auto_out)
            self._cfg["output_folder"] = auto_out
            auto_webp = str(p.parent / f"{p.name}_(webp)")
            self.webp_var.set(auto_webp)
            self._cfg["webp_folder"] = auto_webp
        elif kind == "out":
            self.out_var.set(d)
            self._cfg["output_folder"] = d
            auto_webp = str(p.parent / f"{p.name}_(webp)")
            self.webp_var.set(auto_webp)
            self._cfg["webp_folder"] = auto_webp
        else:
            self.webp_var.set(d)
            self._cfg["webp_folder"] = d
        save_config(self._cfg)

    # ── 상태 복원 ────────────────────────────

    def _restore_state(self):
        if self._cfg.get("input_folder"):  self.in_var.set(self._cfg["input_folder"])
        if self._cfg.get("output_folder"): self.out_var.set(self._cfg["output_folder"])
        if self._cfg.get("webp_folder"):   self.webp_var.set(self._cfg["webp_folder"])

    # ── 프롬프트 조립 ────────────────────────

    def _get_prompt(self):
        parts = [t.get("1.0", "end-1c").strip() for t in self.prompt_texts]
        parts = [p for p in parts if p]
        if self.swap_order.get():
            parts = list(reversed(parts))
        return ", ".join(parts)

    def _get_negative(self):
        parts = [t.get("1.0", "end-1c").strip() for t in self.negative_texts]
        parts = [p for p in parts if p]
        if self.swap_order.get():
            parts = list(reversed(parts))
        return ", ".join(parts)

    # ── ComfyUI 탐지 ─────────────────────────

    def _detect_comfyui(self):
        self.after(0, lambda: self.comfyui_lbl.config(text="탐색 중...", fg=WARNING))
        root = find_comfyui_root()
        if root:
            self._comfyui_out = root / "output"
            self._cfg["comfyui_root"] = str(root)
            save_config(self._cfg)
            self.after(0, lambda: self.comfyui_lbl.config(text=str(root), fg=SUCCESS))
            self._log(f"✅ ComfyUI 발견: {root}", "ok")
        else:
            self._comfyui_out = None
            self.after(0, lambda: self.comfyui_lbl.config(
                text="찾지 못함 — 수동 선택 필요", fg=DANGER))
            self._log("⚠️  ComfyUI 폴더를 찾지 못했습니다.", "warn")
            self.after(0, self._ask_manual_comfyui)

    def _ask_manual_comfyui(self):
        d = filedialog.askdirectory(title="ComfyUI 폴더 선택 (main.py 있는 곳)")
        if not d: return
        if _is_comfyui(Path(d)):
            self._comfyui_out = Path(d) / "output"
            self._cfg["comfyui_root"] = d
            save_config(self._cfg)
            self.comfyui_lbl.config(text=d, fg=SUCCESS)
            self._log(f"✅ ComfyUI 수동 설정: {d}", "ok")
        else:
            messagebox.showerror("오류",
                "ComfyUI 폴더가 아닙니다.\nmain.py + input/ + output/ 이 있어야 합니다.")

    # ── 로그 ────────────────────────────────

    def _log(self, msg, tag=None):
        def _do():
            self.log.configure(state="normal")
            self.log.insert("end", msg + "\n", tag or "")
            self.log.see("end")
            self.log.configure(state="disabled")
        self.after(0, _do)
        if self._log_file:
            try:
                ts = datetime.now().strftime("%H:%M:%S")
                self._log_file.write(f"[{ts}] {msg}\n")
                self._log_file.flush()
            except Exception:
                pass

    def _open_log_file(self):
        if not self.save_log.get():
            return
        try:
            log_dir = Path(__file__).parent / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_path = log_dir / f"batch_log_{stamp}.txt"
            self._log_file = open(log_path, "w", encoding="utf-8")
            self._log_file.write(
                f"ComfyUI 배치 로그 — {datetime.now().isoformat(timespec='seconds')}\n"
                f"{'='*50}\n"
            )
            self._log(f"📝 로그 파일: {log_path}", "dim")
        except Exception as e:
            self._log_file = None
            self._log(f"⚠️  로그 파일 생성 실패: {e}", "warn")

    def _close_log_file(self):
        if self._log_file:
            try: self._log_file.close()
            except Exception: pass
            self._log_file = None

    # ── 설정 저장 헬퍼 ───────────────────────

    def _save_current_settings(self):
        """현재 UI 값들을 config에 저장"""
        try:
            self._cfg["prompt_slots"]   = [t.get("1.0", "end-1c") for t in self.prompt_texts]
            self._cfg["negative_slots"] = [t.get("1.0", "end-1c") for t in self.negative_texts]
            self._cfg["denoise"]        = round(self.denoise_var.get(), 3)
            self._cfg["steps"]          = int(self.steps_var.get())
            self._cfg["bbox_threshold"] = round(self.bbox_var.get(), 3)
            self._cfg["drop_size"]      = int(self.drop_var.get())
        except Exception:
            pass

    # ── 실행 ────────────────────────────────

    def _start(self):
        if not self._comfyui_out:
            self._ask_manual_comfyui(); return
        in_f    = self.in_var.get().strip()
        out_f   = self.out_var.get().strip()
        webp_f  = self.webp_var.get().strip()
        do_webp = self.webp_enabled.get()
        if not in_f:
            self._log("❌ 입력 폴더를 선택해 주세요.", "err"); return
        if not out_f:
            self._log("❌ PNG 출력 폴더를 선택해 주세요.", "err"); return
        if do_webp and not webp_f:
            self._log("❌ WebP 폴더를 선택해 주세요.", "err"); return

        self._save_current_settings()
        save_config(self._cfg)

        self._stop = False
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        threading.Thread(
            target=self._run, args=(in_f, out_f, webp_f, do_webp), daemon=True
        ).start()

    def _stop_now(self):
        self._stop = True
        self._log("⏹ 중단 요청됨...", "err")

    def _ensure_comfyui(self) -> bool:
        try:
            api_get("system_stats")
            self._log(f"✅ ComfyUI 연결 확인  ({BASE_URL})", "ok")
            return True
        except Exception:
            self._log("⚠️  ComfyUI가 꺼져있음 — 자동 실행 시도...", "warn")
            comfyui_root = self._comfyui_out.parent
            proc = start_comfyui(comfyui_root)
            if not proc:
                self._log("❌ ComfyUI 실행 파일을 찾지 못했습니다.", "err")
                return False
            self._log(f"⏳ ComfyUI 시작 중... (최대 {COMFYUI_BOOT_TIMEOUT}초 대기)", "dim")
            if not wait_for_comfyui():
                self._log("❌ ComfyUI 시작 시간 초과. 콘솔 창 확인하세요.", "err")
                return False
            self._log("✅ ComfyUI 자동 시작 완료", "ok")
            return True

    def _update_time(self, idx, total):
        if self._processed == 0:
            self.time_lbl.config(text=f"경과 --:--   남은시간 --:--")
            return
        elapsed = time.time() - self._start_time
        avg = elapsed / self._processed
        remaining = avg * (total - idx)
        self.time_lbl.config(
            text=f"경과 {fmt_time(elapsed)}   남은시간 ~{fmt_time(remaining)}   "
                 f"(이미지당 {avg:.1f}s)"
        )

    def _upload_and_submit(self, img, prompt, negative, denoise, steps,
                           bbox_threshold, drop_size, pre_name=None):
        """업로드(또는 선행업로드 결과 사용) + 워크플로우 제출 → prompt_id"""
        name = pre_name or upload_image(img)
        wf   = build_workflow(name, prompt, negative, denoise, steps,
                              bbox_threshold, drop_size)
        return api_post("prompt", {"prompt": wf})["prompt_id"]

    def _collect_output(self, pid, img, out_path):
        """ComfyUI 출력 대기 + 파일 이동 → dest_png"""
        files    = wait_for_output(pid, self._comfyui_out)
        dest_png = move_to_output(files, img, out_path)
        if not dest_png:
            raise RuntimeError("PNG 이동 실패 (ComfyUI 출력 없음)")
        if KEEP_ALPHA and restore_alpha(dest_png, img):
            self._log("  🫧 투명 배경 복원", "dim")
        return dest_png

    def _do_webp_bg(self, dest_png, webp_path):
        """백그라운드 WebP 변환 (스레드풀에서 실행)"""
        wp_dest = webp_path / f"{dest_png.stem}.webp"
        webp_path.mkdir(parents=True, exist_ok=True)
        result = convert_to_webp(dest_png, wp_dest)
        if result["ok"]:
            self._log(
                f"  🌐 WebP → {wp_dest.name}  "
                f"({fmt_size(result['before'])} → {fmt_size(result['after'])}, "
                f"{result['ratio']:.1f}% 감소)", "webp"
            )
            return True
        else:
            self._log(f"  ⚠️  WebP 변환 실패: {result['error']}", "warn")
            return False

    def _run(self, in_folder, out_folder, webp_folder, do_webp):
        in_path   = Path(in_folder)
        out_path  = Path(out_folder)
        webp_path = Path(webp_folder) if do_webp else None

        prompt   = self._get_prompt()
        negative = self._get_negative()
        denoise  = self.denoise_var.get()
        steps    = int(self.steps_var.get())
        bbox_threshold = self.bbox_var.get()
        drop_size      = int(self.drop_var.get())

        self._open_log_file()

        all_images = sorted(
            [f for f in in_path.rglob("*")
             if f.is_file() and f.suffix.lower() in SUPPORTED_EXT]
        )

        if not all_images:
            self._log("⚠️  처리할 이미지가 없습니다.", "warn")
            self._close_log_file(); self._done(); return

        skipped = 0
        if self.skip_processed.get():
            filtered = []
            for img in all_images:
                rel_dir = img.relative_to(in_path).parent
                png_done = (out_path / rel_dir / f"{img.stem}.png").exists()
                webp_done = True
                if do_webp and webp_path:
                    webp_done = (webp_path / rel_dir / f"{img.stem}.webp").exists()
                if png_done and webp_done:
                    skipped += 1
                else:
                    filtered.append(img)
            images = filtered
        else:
            images = all_images

        if skipped:
            self._log(f"⏭  {skipped}개는 이미 처리됨 — 건너뜀", "dim")

        if not images:
            self._log("✅ 모든 파일이 이미 처리되었습니다.", "ok")
            if self.auto_open.get():
                open_folder(out_path)
            self._close_log_file(); self._done(); return

        if not self._ensure_comfyui():
            self._close_log_file(); self._done(); return

        total = len(images)
        self.after(0, lambda: [
            self.progress1.configure(maximum=total, value=0),
            self.progress2.configure(maximum=total, value=0),
        ])
        self._log(f"🖼️  {total}개 이미지 처리 시작 "
                  f"(prompt='{prompt}', denoise={denoise:.2f}, steps={steps}, "
                  f"bbox={bbox_threshold:.2f}, drop={drop_size})\n", "info")

        ok_up = ok_wp = fail = 0
        self._start_time = time.time()
        self._processed  = 0

        # 파이프라인: WebP 백그라운드 + 다음 이미지 선행 업로드
        webp_pool = ThreadPoolExecutor(max_workers=2) if do_webp else None
        upload_pool = ThreadPoolExecutor(max_workers=1)
        webp_futures = []
        pre_name = None  # 선행 업로드된 파일명

        for idx, img in enumerate(images, 1):
            if self._stop:
                self._log("⏹ 중단됨", "err"); break

            self.after(0, lambda i=idx: self.count_lbl.config(text=f"{i} / {total}"))
            rel = img.relative_to(in_path)
            rel_dir = rel.parent
            display_name = str(rel) if str(rel_dir) != "." else img.name
            self._log(f"[{idx}/{total}] {display_name}")

            sub_out  = out_path / rel_dir
            sub_webp = (webp_path / rel_dir) if webp_path else None

            max_tries = (MAX_RETRIES + 1) if self.retry_on_fail.get() else 1
            success = False
            dest_png = None
            for attempt in range(1, max_tries + 1):
                try:
                    if attempt > 1:
                        self._log(f"  🔁 재시도 {attempt-1}/{MAX_RETRIES}...", "warn")
                        pre_name = None  # 재시도 시 새로 업로드

                    # 1) 업로드 + 워크플로우 제출
                    pid = self._upload_and_submit(
                        img, prompt, negative, denoise, steps,
                        bbox_threshold, drop_size, pre_name)
                    pre_name = None

                    # 2) 다음 이미지 선행 업로드 (GPU 대기 시간 활용)
                    pre_future = None
                    if idx < total and not self._stop:
                        next_img = images[idx]
                        pre_future = upload_pool.submit(upload_image, next_img)

                    # 3) GPU 출력 대기 + 파일 이동
                    dest_png = self._collect_output(pid, img, sub_out)
                    self._log(f"  💾 PNG → {dest_png.name}", "ok")
                    ok_up += 1

                    # 4) 선행 업로드 결과 수거
                    if pre_future:
                        try:
                            pre_name = pre_future.result(timeout=60)
                        except Exception:
                            pre_name = None

                    success = True
                    break
                except Exception as e:
                    self._log(f"  ❌ 오류: {e}", "err")
                    pre_name = None
                    if attempt < max_tries:
                        time.sleep(2)

            if not success:
                fail += 1
                pre_name = None

            # WebP 백그라운드 변환 큐잉
            if success and do_webp and sub_webp and dest_png and dest_png.exists():
                webp_futures.append(
                    webp_pool.submit(self._do_webp_bg, dest_png, sub_webp))

            self._processed += 1
            self.after(0, lambda i=idx: [
                self.progress1.configure(value=i),
                self.progress2.configure(value=i),
                self.webp_lbl.config(text=f"{i} / {total}"),
                self._update_time(i, total)
            ])

        # 남은 WebP 변환 완료 대기
        if webp_futures:
            remaining = sum(1 for f in webp_futures if not f.done())
            if remaining:
                self._log(f"⏳ 남은 WebP {remaining}개 변환 대기 중...", "dim")
            for f in webp_futures:
                try:
                    if f.result(): ok_wp += 1
                except Exception:
                    pass
        if webp_pool:
            webp_pool.shutdown(wait=True)
        upload_pool.shutdown(wait=True)

        self._log(f"\n{'─'*50}", "dim")
        self._log(
            f"완료  ✅ 업스케일 {ok_up}   🌐 WebP {ok_wp}   "
            f"⏭ 건너뜀 {skipped}   ❌ 실패 {fail}",
            "ok" if fail == 0 else "warn"
        )
        elapsed = time.time() - self._start_time
        self._log(f"총 소요시간 {fmt_time(elapsed)}", "dim")

        self._close_log_file()

        if self.auto_open.get() and not self._stop:
            open_folder(out_path)

        self._done()

    def _done(self):
        self.after(0, lambda: (
            self.start_btn.config(state="normal"),
            self.stop_btn.config(state="disabled")
        ))

    def _on_close(self):
        self._save_current_settings()
        self._close_log_file()
        save_config(self._cfg)
        self.destroy()


if __name__ == "__main__":
    App().mainloop()