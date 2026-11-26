"""Crossing-event-only Re-Identification & strict 2-student target matching."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import cv2
import numpy as np
from scipy.optimize import linear_sum_assignment

from edge.src.config import ReIDConfig
from edge.src.tracker import TrackedObject


@dataclass
class EnrolledProfile:
    person_id: str
    display_name: str
    custom_class_label: str = ""
    face_embeddings: List[np.ndarray] = field(default_factory=list)
    appearance_embeddings: List[np.ndarray] = field(default_factory=list)
    session_appearance: Optional[np.ndarray] = None


@dataclass
class ReIDMatchResult:
    matched: bool
    person_id: Optional[str]
    display_name: str
    confidence: float
    method: str
    ignored_bystander: bool = False


class CrossingReIDMatcher:
    """Matches identities ONLY when a doorway crossing event occurs."""

    def __init__(
        self,
        config: ReIDConfig,
        custom_class_map: Optional[Dict[str, str]] = None,
    ) -> None:
        self.config = config
        self.custom_class_map = {k.lower(): v for k, v in (custom_class_map or {}).items()}
        self._face_detector = None
        self._face_recognizer = None
        self._reid_ort = None
        self._profiles: Dict[str, EnrolledProfile] = {}
        self._track_to_person: Dict[Tuple[str, int], Tuple[str, float]] = {}
        self._room1_recent_gallery: Dict[str, Tuple[np.ndarray, float, str]] = {}
        self._next_anon_num = 1

        self._load_optional_models()
        self._load_enrolled_profiles()

    def _load_optional_models(self) -> None:
        fd_path = Path(self.config.face_detector_onnx)
        fr_path = Path(self.config.face_recognizer_onnx)
        if fd_path.is_file() and fr_path.is_file() and hasattr(cv2, "FaceDetectorYN"):
            self._face_detector = cv2.FaceDetectorYN.create(str(fd_path), "", (320, 320))
            self._face_recognizer = cv2.FaceRecognizerSF.create(str(fr_path), "")

        reid_path = Path(self.config.appearance_reid_onnx)
        if reid_path.is_file():
            import onnxruntime as ort
            self._reid_ort = ort.InferenceSession(str(reid_path), providers=["CPUExecutionProvider"])

    def _load_enrolled_profiles(self) -> None:
        for pid, prof_cfg in self.config.enrolled_students.items():
            profile = EnrolledProfile(
                person_id=pid,
                display_name=prof_cfg.display_name,
                custom_class_label=prof_cfg.custom_class_label.lower(),
            )
            if prof_cfg.references_dir:
                ref_dir = Path(prof_cfg.references_dir)
                if ref_dir.is_dir():
                    for img_file in sorted(ref_dir.iterdir()):
                        if img_file.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp"}:
                            bgr = cv2.imread(str(img_file))
                            if bgr is not None:
                                self._add_reference_crop(profile, bgr)
            self._profiles[pid] = profile

    def register_student_reference(
        self,
        person_id: str,
        display_name: str,
        reference_bgr: np.ndarray,
        custom_class_label: str = "",
    ) -> None:
        """Enroll or append a reference image for a target student at runtime."""
        if person_id not in self._profiles:
            self._profiles[person_id] = EnrolledProfile(
                person_id=person_id,
                display_name=display_name,
                custom_class_label=custom_class_label.lower(),
            )
        self._add_reference_crop(self._profiles[person_id], reference_bgr)

    def _add_reference_crop(self, profile: EnrolledProfile, bgr: np.ndarray) -> None:
        face_vec = self.extract_face_embedding(bgr)
        if face_vec is not None:
            profile.face_embeddings.append(face_vec)
        app_vec = self.extract_appearance_embedding(bgr)
        if app_vec is not None:
            profile.appearance_embeddings.append(app_vec)

    def extract_face_embedding(self, crop_bgr: Optional[np.ndarray]) -> Optional[np.ndarray]:
        if crop_bgr is None or crop_bgr.size == 0 or self._face_detector is None or self._face_recognizer is None:
            return None
        h, w = crop_bgr.shape[:2]
        if h < 32 or w < 32:
            return None
        self._face_detector.setInputSize((w, h))
        _, faces = self._face_detector.detect(crop_bgr)
        if faces is None or len(faces) == 0:
            return None
        aligned = self._face_recognizer.alignCrop(crop_bgr, faces[0])
        feat = self._face_recognizer.feature(aligned).flatten().astype(np.float32)
        return _l2_normalize(feat)

    def extract_appearance_embedding(self, crop_bgr: Optional[np.ndarray]) -> Optional[np.ndarray]:
        """Extract L2-normalized upper/mid/lower HSV+BGR spatial appearance embedding."""
        if crop_bgr is None or crop_bgr.size == 0:
            return None

        if self._reid_ort is not None:
            resized = cv2.resize(cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB), (128, 256))
            arr = (resized.astype(np.float32) / 255.0 - np.array([0.485, 0.456, 0.406], dtype=np.float32)) / np.array([0.229, 0.224, 0.225], dtype=np.float32)
            nchw = np.transpose(arr, (2, 0, 1))[None, ...].astype(np.float32)
            inp_name = self._reid_ort.get_inputs()[0].name
            vec = self._reid_ort.run(None, {inp_name: nchw})[0].flatten().astype(np.float32)
            return _l2_normalize(vec)

        # Zero-dependency fallback: central crop ignoring edge background + 3 vertical bands
        resized = cv2.resize(crop_bgr, (64, 128), interpolation=cv2.INTER_AREA)
        center = resized[:, 10:54, :]
        hsv = cv2.cvtColor(center, cv2.COLOR_BGR2HSV)
        parts_hsv = [hsv[0:36, :], hsv[36:82, :], hsv[82:128, :]]
        parts_bgr = [center[0:36, :], center[36:82, :], center[82:128, :]]
        feats: List[np.ndarray] = []
        for h_band, b_band in zip(parts_hsv, parts_bgr):
            # Mask out low-saturation neutral gray background pixels when present
            fg_mask = cv2.inRange(h_band, np.array([0, 25, 20]), np.array([180, 255, 255]))
            use_mask = fg_mask if int(np.count_nonzero(fg_mask)) >= 20 else None
            hs_hist = cv2.calcHist([h_band], [0, 1], use_mask, [18, 4], [0, 180, 25, 256]).flatten()
            hs_hist = _l2_normalize(hs_hist)
            mean_bgr = cv2.mean(b_band, mask=use_mask)[:3]
            bgr_norm = _l2_normalize(np.asarray(mean_bgr, dtype=np.float32))
            feats.append(hs_hist * 0.75)
            feats.append(bgr_norm * 0.25)
        vec = np.concatenate(feats).astype(np.float32)
        return _l2_normalize(vec)

    def match_crossing(
        self,
        camera_id: str,
        tracked_object: TrackedObject,
        direction: str,
        timestamp_epoch: float,
    ) -> ReIDMatchResult:
        """Identify a person when a LineZone crossing event fires."""
        cache_key = (camera_id, tracked_object.track_id)
        crop = tracked_object.sharpest_crop_bgr
        app_vec = self.extract_appearance_embedding(crop)
        face_vec = self.extract_face_embedding(crop)
        cname_lower = tracked_object.class_name.lower()

        # Reuse track-locked identity if already verified for this track
        if cache_key in self._track_to_person:
            pid, cached_conf = self._track_to_person[cache_key]
            prof = self._profiles.get(pid)
            display = prof.display_name if prof else f"Person {pid}"
            if app_vec is not None and prof is not None:
                prof.session_appearance = _blend_ema(prof.session_appearance, app_vec)
            if camera_id == "cam_1" and app_vec is not None:
                self._room1_recent_gallery[pid] = (app_vec, timestamp_epoch, display)
            return ReIDMatchResult(
                matched=True,
                person_id=pid,
                display_name=display,
                confidence=cached_conf,
                method="track_lock",
            )

        # Strict 2-Student Target Mode (Option 1 + Option 2 hybrid)
        if self.config.strict_target_mode and self._profiles:
            result = self._match_against_enrolled(
                tracked_object=tracked_object,
                cname_lower=cname_lower,
                face_vec=face_vec,
                app_vec=app_vec,
            )
            if result.matched and result.person_id and result.person_id != "UNKNOWN_TRACK":
                self._track_to_person[cache_key] = (result.person_id, result.confidence)
                prof = self._profiles.get(result.person_id)
                if prof is not None and app_vec is not None:
                    prof.session_appearance = _blend_ema(prof.session_appearance, app_vec)
                if camera_id == "cam_1" and app_vec is not None:
                    self._room1_recent_gallery[result.person_id] = (app_vec, timestamp_epoch, result.display_name)
            return result

        # General Multi-Person Mode (strict_target_mode=False)
        if camera_id == "cam_2":
            best_pid, best_score, best_display = self._match_recent_room1(app_vec, timestamp_epoch)
            if best_pid is not None and best_score >= self.config.appearance_match_threshold:
                self._track_to_person[cache_key] = (best_pid, best_score)
                return ReIDMatchResult(
                    matched=True,
                    person_id=best_pid,
                    display_name=best_display,
                    confidence=best_score,
                    method="cross_camera_appearance",
                )
            return ReIDMatchResult(
                matched=True,
                person_id="UNKNOWN_TRACK",
                display_name="Unknown Person",
                confidence=best_score,
                method="unknown_fallback",
            )

        # cam_1 in general mode: match existing session gallery or assign new track ID
        best_pid, best_score, best_display = self._match_recent_room1(app_vec, timestamp_epoch)
        if best_pid is not None and best_score >= self.config.appearance_match_threshold:
            self._track_to_person[cache_key] = (best_pid, best_score)
            if app_vec is not None:
                self._room1_recent_gallery[best_pid] = (app_vec, timestamp_epoch, best_display)
            return ReIDMatchResult(
                matched=True,
                person_id=best_pid,
                display_name=best_display,
                confidence=best_score,
                method="appearance_gallery",
            )

        new_pid = f"P{self._next_anon_num:03d}"
        self._next_anon_num += 1
        display = f"Person {new_pid}"
        self._track_to_person[cache_key] = (new_pid, float(tracked_object.confidence))
        if app_vec is not None:
            self._room1_recent_gallery[new_pid] = (app_vec, timestamp_epoch, display)
        return ReIDMatchResult(
            matched=True,
            person_id=new_pid,
            display_name=display,
            confidence=float(tracked_object.confidence),
            method="new_session_id",
        )

    def match_frame_batch(self, tracked_objects: List[TrackedObject]) -> Dict[int, ReIDMatchResult]:
        """1-to-1 Hungarian assignment of candidate tracks to enrolled target students (never duplicates)."""
        if not tracked_objects or not self._profiles:
            return {}

        pids = list(self._profiles.keys())
        score_mat = np.zeros((len(tracked_objects), len(pids)), dtype=np.float32)

        for r, obj in enumerate(tracked_objects):
            crop = obj.sharpest_crop_bgr
            face_vec = self.extract_face_embedding(crop)
            app_vec = self.extract_appearance_embedding(crop)
            cname_lower = obj.class_name.lower()
            for c, pid in enumerate(pids):
                score_mat[r, c] = self._score_candidate(
                    self._profiles[pid], cname_lower, float(obj.confidence), face_vec, app_vec
                )

        row_ind, col_ind = linear_sum_assignment(-score_mat)
        assigned: Dict[int, ReIDMatchResult] = {}
        for r, c in zip(row_ind, col_ind):
            score = float(score_mat[r, c])
            row_scores = sorted([float(x) for x in score_mat[r]], reverse=True)
            margin = (row_scores[0] - row_scores[1]) if len(row_scores) > 1 else row_scores[0]
            obj = tracked_objects[r]
            pid = pids[c]
            prof = self._profiles[pid]
            if score >= self.config.combined_match_threshold and (
                len(pids) == 1 or margin >= self.config.match_margin_threshold
            ):
                assigned[obj.track_id] = ReIDMatchResult(
                    matched=True,
                    person_id=pid,
                    display_name=prof.display_name,
                    confidence=score,
                    method="hungarian_1to1",
                )
        return assigned

    def _match_against_enrolled(
        self,
        tracked_object: TrackedObject,
        cname_lower: str,
        face_vec: Optional[np.ndarray],
        app_vec: Optional[np.ndarray],
    ) -> ReIDMatchResult:
        scores: List[Tuple[str, float]] = []
        for pid, prof in self._profiles.items():
            score = self._score_candidate(
                prof, cname_lower, float(tracked_object.confidence), face_vec, app_vec
            )
            scores.append((pid, score))

        scores.sort(key=lambda item: item[1], reverse=True)
        best_pid, best_score = scores[0]
        second_score = scores[1][1] if len(scores) > 1 else 0.0
        margin = best_score - second_score

        if best_score >= self.config.combined_match_threshold and (
            len(scores) == 1 or margin >= self.config.match_margin_threshold
        ):
            prof = self._profiles[best_pid]
            return ReIDMatchResult(
                matched=True,
                person_id=best_pid,
                display_name=prof.display_name,
                confidence=best_score,
                method="enrolled_hybrid",
            )

        if self.config.allow_unknown_fallback:
            return ReIDMatchResult(
                matched=True,
                person_id="UNKNOWN_TRACK",
                display_name="Unknown Person",
                confidence=best_score,
                method="unknown_fallback",
            )

        return ReIDMatchResult(
            matched=False,
            person_id=None,
            display_name="Ignored Bystander",
            confidence=best_score,
            method="bystander_rejected",
            ignored_bystander=True,
        )

    def _score_candidate(
        self,
        profile: EnrolledProfile,
        cname_lower: str,
        det_conf: float,
        face_vec: Optional[np.ndarray],
        app_vec: Optional[np.ndarray],
    ) -> float:
        weights = 0.0
        weighted_sum = 0.0

        # Option 2 signal: custom VEXDET class label (e.g., "student_1" -> "001")
        mapped_pid = self.custom_class_map.get(cname_lower)
        if mapped_pid is not None or (profile.custom_class_label and cname_lower == profile.custom_class_label):
            cls_match = 1.0 if (mapped_pid == profile.person_id or cname_lower == profile.custom_class_label) else 0.0
            weighted_sum += 0.40 * (cls_match * det_conf)
            weights += 0.40

        # Option 1 signal A: face embedding similarity
        if face_vec is not None and profile.face_embeddings:
            face_sim = max(float(np.dot(face_vec, ref)) for ref in profile.face_embeddings)
            face_norm = float(np.clip((face_sim + 0.1) / 1.1, 0.0, 1.0))
            weighted_sum += 0.45 * face_norm
            weights += 0.45

        # Option 1 signal B: reference + session appearance similarity
        app_refs = list(profile.appearance_embeddings)
        if profile.session_appearance is not None:
            app_refs.append(profile.session_appearance)
        if app_vec is not None and app_refs:
            app_sim = max(float(np.dot(app_vec, ref)) for ref in app_refs)
            # Gate weak appearance matches when no face or custom-class evidence supports them
            if face_vec is None and weights == 0.0 and app_sim < self.config.appearance_match_threshold:
                return 0.0
            app_norm = float(np.clip(app_sim, 0.0, 1.0))
            weighted_sum += 0.35 * app_norm
            weights += 0.35

        return float(weighted_sum / weights) if weights > 0.0 else 0.0

    def _match_recent_room1(
        self,
        app_vec: Optional[np.ndarray],
        timestamp_epoch: float,
    ) -> Tuple[Optional[str], float, str]:
        if app_vec is None:
            return None, 0.0, "Unknown Person"

        best_pid: Optional[str] = None
        best_score = 0.0
        best_display = "Unknown Person"
        for pid, (ref_vec, ref_ts, disp) in list(self._room1_recent_gallery.items()):
            if (timestamp_epoch - ref_ts) > max(self.config.cross_camera_window_seconds, 3600.0):
                continue
            sim = float(np.dot(app_vec, ref_vec))
            if sim > best_score:
                best_score = sim
                best_pid = pid
                best_display = disp
        return best_pid, best_score, best_display


def _l2_normalize(vec: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vec))
    return (vec / norm).astype(np.float32) if norm > 1e-8 else vec.astype(np.float32)


def _blend_ema(prev: Optional[np.ndarray], curr: np.ndarray, alpha: float = 0.35) -> np.ndarray:
    if prev is None:
        return curr
    return _l2_normalize((1.0 - alpha) * prev + alpha * curr)
