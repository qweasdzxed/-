import os
import json
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QTextEdit, QTabWidget, QTreeWidget,
    QTreeWidgetItem, QLabel, QSizePolicy,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QImage


def read_sd_prompt(path: str) -> dict[str, str]:
    """Read Stable Diffusion / ComfyUI prompt metadata from image."""
    result = {}
    try:
        from PIL import Image as PILImage
        from PIL.PngImagePlugin import PngInfo
        img = PILImage.open(path)
        if hasattr(img, "text"):
            for key, val in img.text.items():
                if key in ("parameters", "prompt", "workflow", "negative_prompt"):
                    result[key] = val
                elif key.lower().startswith("comfy"):
                    result[key] = val
        info = img.info or {}
        if "parameters" in info and "parameters" not in result:
            result["parameters"] = str(info["parameters"])
        if "prompt" in info and "prompt" not in result:
            result["prompt"] = str(info["prompt"])
        if "workflow" in info and "workflow" not in result:
            result["workflow"] = str(info["workflow"])
    except Exception:
        pass
    return result


def read_exif(path: str) -> list[tuple[str, str]]:
    """Read all EXIF tags from image."""
    items = []
    try:
        from PIL import Image as PILImage
        from PIL.ExifTags import TAGS, GPSTAGS
        img = PILImage.open(path)
        exif = img.getexif()
        if not exif:
            return items
        for tag_id, val in exif.items():
            tag_name = TAGS.get(tag_id, f"Tag_{tag_id}")
            if isinstance(val, bytes):
                if len(val) > 100:
                    val = f"<{len(val)} bytes>"
                else:
                    try:
                        val = val.decode("utf-8", errors="replace")
                    except Exception:
                        val = repr(val)
            items.append((str(tag_name), str(val)))
        for ifd_key in [0x8769, 0x8825, 0xA005]:
            try:
                ifd = exif.get_ifd(ifd_key)
                tag_map = GPSTAGS if ifd_key == 0x8825 else TAGS
                for tag_id, val in ifd.items():
                    tag_name = tag_map.get(tag_id, f"Tag_{tag_id}")
                    if isinstance(val, bytes):
                        if len(val) > 100:
                            val = f"<{len(val)} bytes>"
                        else:
                            try:
                                val = val.decode("utf-8", errors="replace")
                            except Exception:
                                val = repr(val)
                    items.append((str(tag_name), str(val)))
            except Exception:
                continue
    except Exception:
        pass
    return items


def read_xmp(path: str) -> str:
    """Read raw XMP data from image."""
    try:
        from PIL import Image as PILImage
        img = PILImage.open(path)
        xmp = img.info.get("xmp", img.info.get("XML:com.adobe.xmp", b""))
        if isinstance(xmp, bytes):
            xmp = xmp.decode("utf-8", errors="replace")
        return xmp if xmp else ""
    except Exception:
        return ""


def get_exif_thumbnail(path: str) -> QPixmap | None:
    """Extract embedded EXIF thumbnail."""
    try:
        from PIL import Image as PILImage
        img = PILImage.open(path)
        exif = img.getexif()
        if not exif:
            return None
        thumb_ifd = exif.get_ifd(0x0201)
        if not thumb_ifd:
            thumb_data = None
            for tag_id in [0x0201]:
                if tag_id in exif:
                    thumb_data = exif[tag_id]
                    break
            if not thumb_data:
                return None
    except Exception:
        pass
    return None


class MetadataPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._tabs = QTabWidget()

        self._prompt_text = QTextEdit()
        self._prompt_text.setReadOnly(True)

        self._exif_tree = QTreeWidget()
        self._exif_tree.setHeaderLabels(["태그", "값"])
        self._exif_tree.setAlternatingRowColors(True)
        self._exif_tree.setColumnWidth(0, 160)

        self._xmp_text = QTextEdit()
        self._xmp_text.setReadOnly(True)

        self._tabs.addTab(self._prompt_text, "SD 프롬프트")
        self._tabs.addTab(self._exif_tree, "EXIF")
        self._tabs.addTab(self._xmp_text, "XMP")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._tabs)

        self.setStyleSheet("""
            QTabWidget::pane { border: none; }
            QTabBar::tab {
                background: #2d2d2d; color: #ccc;
                padding: 6px 12px; border: none;
            }
            QTabBar::tab:selected { background: #0078d4; color: #fff; }
            QTextEdit {
                background-color: #1e1e1e; color: #ddd;
                border: none; font-family: Consolas, monospace; font-size: 12px;
            }
            QTreeWidget {
                background-color: #1e1e1e; color: #ddd;
                border: none; font-size: 12px;
            }
            QTreeWidget::item:alternate { background-color: #252525; }
            QHeaderView::section {
                background-color: #2d2d2d; color: #ccc;
                padding: 4px; border: none;
            }
        """)

    def load_metadata(self, path: str):
        self._prompt_text.clear()
        self._exif_tree.clear()
        self._xmp_text.clear()

        sd = read_sd_prompt(path)
        if sd:
            parts = []
            for key, val in sd.items():
                try:
                    parsed = json.loads(val)
                    val = json.dumps(parsed, indent=2, ensure_ascii=False)
                except (json.JSONDecodeError, TypeError):
                    pass
                parts.append(f"=== {key} ===\n{val}")
            self._prompt_text.setPlainText("\n\n".join(parts))
        else:
            self._prompt_text.setPlainText("SD/ComfyUI 프롬프트 정보가 없습니다.")

        exif_items = read_exif(path)
        if exif_items:
            for tag, val in exif_items:
                item = QTreeWidgetItem([tag, val])
                self._exif_tree.addTopLevelItem(item)
        else:
            item = QTreeWidgetItem(["(없음)", "EXIF 정보가 없습니다"])
            self._exif_tree.addTopLevelItem(item)

        xmp = read_xmp(path)
        if xmp:
            self._xmp_text.setPlainText(xmp)
        else:
            self._xmp_text.setPlainText("XMP 정보가 없습니다.")
