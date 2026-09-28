import json
import joblib
import numpy as np
from pathlib import Path
from tensorflow.keras.models import load_model

MODELS_DIR = Path(__file__).resolve().parent.parent.parent / "models_store"

with open(MODELS_DIR / "inference_config.json") as f:
    _config = json.load(f)

THRESHOLD = _config["decision_threshold"]

_MODEL_FILES = [
    "CNN_BiLSTM_Final.keras",
    "CNN_BiLSTM_Attention_Final.keras",
    "CNN_BiLSTM_MultiHeadAttention_Final.keras",
    "Improved_CNN_BiLSTM_Final.keras",
]

_models = [load_model(MODELS_DIR / name) for name in _MODEL_FILES]
_scaler = joblib.load(MODELS_DIR / "standard_scaler.pkl")

def run_ensemble(window: np.ndarray) -> float:
    """window shape: (512, 6), raw sensor units -> returns averaged fall probability"""
    flat = window.reshape(-1, 6)          # (512, 6) -> (512, 6), matches scaler's fit shape
    scaled_flat = _scaler.transform(flat)  # apply the SAME scaling used during training
    scaled_window = scaled_flat.reshape(1, 512, 6)

    predictions = [model.predict(scaled_window, verbose=0)[0][0] for model in _models]
    return float(np.mean(predictions))