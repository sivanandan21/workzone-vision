"""Configuration loader for the WorkZone Vision edge runtime."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Tuple
from pydantic import BaseModel, Field, field_validator
import yaml

ALLOWED_VEXDET_MODELS = ("RFDETRNano", "RFDETRSmall", "RFDETRMedium", "RFDETRLarge")


class TripwireConfig(BaseModel):
    normalized: bool = True
    p1: Tuple[float, float] = (0.2, 0.55)
    p2: Tuple[float, float] = (0.8, 0.55)
    inward_normal: Tuple[float, float] = (0.0, 1.0)
    debounce_seconds: float = Field(default=4.0, ge=1.0, le=30.0)
    deadband_ratio: float = Field(default=0.02, ge=0.0, le=0.2)


class CameraConfig(BaseModel):
    id: str
    name: str
    room: str
    source: str
    target_fps: float = Field(default=8.0, gt=0.1, le=60.0)
    tripwire: TripwireConfig = Field(default_factory=TripwireConfig)


class VexdetEdgeConfig(BaseModel):
    onnx_path: str = "models/vexdet.onnx"
    model_name: str = "RFDETRNano"
    target_classes: List[str] = Field(default_factory=lambda: ["person"])
    custom_class_map: Dict[str, str] = Field(default_factory=lambda: {"student_1": "001", "student_2": "002"})
    confidence_threshold: float = Field(default=0.40, ge=0.05, le=0.99)
    dedup_iou_threshold: float = Field(default=0.50, ge=0.1, le=0.95)
    blur_threshold: float = Field(default=100.0, ge=0.0)
    enable_clahe: bool = True
    clahe_clip_limit: float = Field(default=2.0, ge=0.1, le=20.0)
    clahe_tile_grid: Tuple[int, int] = (8, 8)
    input_size: Tuple[int, int] = (560, 560)

    @field_validator("model_name")
    @classmethod
    def validate_apache_model(cls, value: str) -> str:
        if value not in ALLOWED_VEXDET_MODELS:
            raise ValueError(
                f"Disallowed model '{value}'. Only Apache-2.0 VEXDET RF-DETR models "
                f"{ALLOWED_VEXDET_MODELS} are permitted."
            )
        return value


class EnrolledStudentProfileConfig(BaseModel):
    display_name: str
    custom_class_label: str = ""
    references_dir: str = ""


class ReIDConfig(BaseModel):
    strict_target_mode: bool = True
    allow_unknown_fallback: bool = False
    face_match_threshold: float = 0.42
    appearance_match_threshold: float = 0.68
    combined_match_threshold: float = 0.58
    match_margin_threshold: float = 0.06
    cross_camera_window_seconds: float = 15.0
    face_detector_onnx: str = "models/face_detection_yunet_2023mar.onnx"
    face_recognizer_onnx: str = "models/face_recognition_sface_2021dec.onnx"
    appearance_reid_onnx: str = "models/reid_appearance.onnx"
    enrolled_students: Dict[str, EnrolledStudentProfileConfig] = Field(default_factory=dict)


class CloudConfig(BaseModel):
    transport: str = "mock"  # "mock" | "https" | "iot_mqtt"
    api_endpoint: str = "http://localhost:8000"
    api_key: str = ""
    iot_endpoint: str = ""
    iot_topic: str = "workzone/crossings"
    iot_cert_path: str = ""
    iot_key_path: str = ""
    iot_ca_path: str = ""
    away_alert_threshold_seconds: float = 900.0
    offline_db_path: str = "edge/data/outbox.db"
    snapshots_dir: str = "edge/data/snapshots"
    local_state_db_path: str = "edge/data/local_state.db"


class SystemConfig(BaseModel):
    vexdet: VexdetEdgeConfig = Field(default_factory=VexdetEdgeConfig)
    cameras: Dict[str, CameraConfig] = Field(default_factory=dict)
    reid: ReIDConfig = Field(default_factory=ReIDConfig)
    cloud: CloudConfig = Field(default_factory=CloudConfig)


def load_system_config(config_path: str = None) -> SystemConfig:
    """Load YAML configuration and apply environment variable overrides."""
    path_str = config_path or os.environ.get("WORKZONE_CONFIG_PATH", "configs/system_config.yaml")
    path = Path(path_str)
    data = {}
    if path.is_file():
        with open(path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}

    cfg = SystemConfig(**data)

    # Apply explicit environment overrides
    if os.environ.get("VEXDET_ONNX_PATH"):
        cfg.vexdet.onnx_path = os.environ["VEXDET_ONNX_PATH"]
    if os.environ.get("VEXDET_MODEL_NAME"):
        cfg.vexdet.model_name = VexdetEdgeConfig.validate_apache_model(os.environ["VEXDET_MODEL_NAME"])
    if os.environ.get("VEXDET_CONF_THRESHOLD"):
        cfg.vexdet.confidence_threshold = float(os.environ["VEXDET_CONF_THRESHOLD"])
    if os.environ.get("VEXDET_BLUR_THRESHOLD"):
        cfg.vexdet.blur_threshold = float(os.environ["VEXDET_BLUR_THRESHOLD"])
    if os.environ.get("STRICT_TARGET_MODE"):
        cfg.reid.strict_target_mode = os.environ["STRICT_TARGET_MODE"].strip().lower() in ("1", "true", "yes")
    if os.environ.get("ALLOW_UNKNOWN_FALLBACK"):
        cfg.reid.allow_unknown_fallback = os.environ["ALLOW_UNKNOWN_FALLBACK"].strip().lower() in ("1", "true", "yes")
    if os.environ.get("CAM_1_SOURCE") and "cam_1" in cfg.cameras:
        cfg.cameras["cam_1"].source = os.environ["CAM_1_SOURCE"]
    if os.environ.get("CAM_2_SOURCE") and "cam_2" in cfg.cameras:
        cfg.cameras["cam_2"].source = os.environ["CAM_2_SOURCE"]
    if os.environ.get("WORKZONE_TRANSPORT"):
        cfg.cloud.transport = os.environ["WORKZONE_TRANSPORT"]
    if os.environ.get("AWS_API_ENDPOINT"):
        cfg.cloud.api_endpoint = os.environ["AWS_API_ENDPOINT"]
    if os.environ.get("AWS_API_KEY"):
        cfg.cloud.api_key = os.environ["AWS_API_KEY"]
    if os.environ.get("AWAY_ALERT_THRESHOLD_SECONDS"):
        cfg.cloud.away_alert_threshold_seconds = float(os.environ["AWAY_ALERT_THRESHOLD_SECONDS"])

    return cfg
