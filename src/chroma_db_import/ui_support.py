from __future__ import annotations

from PySide6.QtWidgets import QApplication, QWidget
from chroma_db_import.ui_models import DeviceOption

TORCH_CUDA_INDEX_URL = "https://download.pytorch.org/whl/cu128"

def embedding_device_options() -> tuple[list[DeviceOption], str]:
    options = [DeviceOption("Auto", "auto"), DeviceOption("CPU", "cpu")]
    try:
        import torch
    except ImportError as exc:
        return options, (
            "GPU unavailable: PyTorch is not installed in this environment. "
            f"Auto will use CPU. Import error: {exc}"
        )

    torch_version = getattr(torch, "__version__", "unknown")
    cuda_version = getattr(getattr(torch, "version", None), "cuda", None) or "not included"
    try:
        cuda_available = bool(torch.cuda.is_available())
    except Exception as exc:
        return options, (
            f"GPU unavailable: PyTorch {torch_version} could not query CUDA. "
            f"Auto will use CPU. CUDA runtime reported: {exc}"
        )

    if not cuda_available:
        return options, (
            f"GPU unavailable: PyTorch {torch_version} reports CUDA is not available. "
            f"PyTorch CUDA build/runtime: {cuda_version}. Auto will use CPU. "
            "This usually means the installed PyTorch build is CPU-only, the NVIDIA driver/CUDA "
            "runtime is not visible, or no compatible NVIDIA GPU is available."
        )

    try:
        device_count = int(torch.cuda.device_count())
    except Exception as exc:
        return options, (
            f"GPU unavailable: PyTorch {torch_version} reports CUDA support, but device enumeration failed. "
            f"Auto will use CPU. Error: {exc}"
        )

    if device_count <= 0:
        return options, (
            f"GPU unavailable: PyTorch {torch_version} reports CUDA support but no CUDA devices. "
            f"PyTorch CUDA build/runtime: {cuda_version}. Auto will use CPU."
        )

    names: list[str] = []
    for index in range(device_count):
        try:
            name = str(torch.cuda.get_device_name(index))
        except Exception as exc:
            names.append(f"CUDA device {index} (name unavailable: {exc})")
            continue
        names.append(name)
        options.append(DeviceOption(name, f"cuda:{index}"))

    return options, (
        f"GPU available: PyTorch {torch_version} can use {device_count} CUDA device(s): "
        f"{', '.join(names)}. Auto will use {names[0]}."
    )

def normalize_embedding_device_value(value: str) -> str:
    device = value.strip().lower()
    if not device:
        return "auto"
    if device == "cuda":
        return "cuda:0"
    return device

def pytorch_cuda_is_available() -> bool:
    try:
        import torch
    except Exception:
        return False
    try:
        return bool(torch.cuda.is_available()) and int(torch.cuda.device_count()) > 0
    except Exception:
        return False

def resolve_embedding_device(value: str) -> str:
    device = normalize_embedding_device_value(value)
    if device != "auto":
        return device
    options, _diagnostic = embedding_device_options()
    for option in options:
        if option.value.startswith("cuda"):
            return option.value
    return "cpu"

def is_descendant_of(widget: QWidget, ancestor: QWidget) -> bool:
    parent = widget.parent()
    while parent is not None:
        if parent is ancestor:
            return True
        parent = parent.parent()
    return False

def apply_dark_theme(app: QApplication) -> None:
    app.setStyleSheet(
        """
        QWidget {
            background: #101318;
            color: #e5e7eb;
            font-family: Segoe UI;
            font-size: 10.5pt;
        }
        QToolBar {
            background: #1a1e26;
            border-bottom: 1px solid #39414f;
            padding: 6px;
            spacing: 8px;
        }
        QTreeWidget, QTreeView, QListWidget {
            background: #171c24;
            border: 1px solid #39414f;
            border-radius: 6px;
            padding: 8px;
        }
        QTreeWidget::item, QListWidget::item {
            min-height: 30px;
            border-radius: 4px;
            padding: 4px;
        }
        QTreeWidget::item:selected, QListWidget::item:selected, QTreeView::item:selected {
            background: #2f6feb;
            color: white;
        }
        QLabel {
            padding: 2px;
        }
        QLineEdit, QPlainTextEdit, QComboBox {
            background: #171c24;
            border: 1px solid #445061;
            border-radius: 6px;
            padding: 8px;
            selection-background-color: #2f6feb;
        }
        QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QTreeWidget:focus, QTreeView:focus, QListWidget:focus {
            border: 1px solid #5a6a81;
        }
        QComboBox::drop-down {
            border: none;
            width: 28px;
        }
        QComboBox QAbstractItemView {
            background: #171c24;
            border: 1px solid #445061;
            selection-background-color: #2f6feb;
        }
        QProgressBar {
            background: #171c24;
            border: 1px solid #445061;
            border-radius: 6px;
            color: #e5e7eb;
            min-height: 18px;
            text-align: center;
        }
        QProgressBar::chunk {
            background: #2f6feb;
            border-radius: 5px;
        }
        QPushButton {
            background: #2f6feb;
            border: none;
            border-radius: 6px;
            color: white;
            font-weight: 600;
            padding: 9px 16px;
        }
        QPushButton:disabled {
            background: #1f2630;
            color: #6b7280;
        }
        QPushButton#infoButton {
            background: #232a36;
            border: 1px solid #445061;
            border-radius: 11px;
            color: #9ca3af;
            font-weight: 700;
            min-width: 22px;
            max-width: 22px;
            min-height: 22px;
            max-height: 22px;
            padding: 0;
        }
        QPushButton#infoButton:hover {
            background: #2f6feb;
            color: white;
        }
        QCheckBox {
            spacing: 8px;
            min-height: 26px;
        }
        QScrollArea {
            border: none;
        }
        QSplitter::handle {
            background: #39414f;
        }
        """
    )
