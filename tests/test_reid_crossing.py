"""Unit tests for crossing-event ReID, strict 2-student filtering, and 1-to-1 anti-duplicate matching."""

from __future__ import annotations

import cv2
import numpy as np

from edge.src.config import EnrolledStudentProfileConfig, ReIDConfig
from edge.src.reid import CrossingReIDMatcher
from edge.src.tracker import TrackedObject


def _solid_person_crop(shirt_bgr, pants_bgr) -> np.ndarray:
    img = np.full((128, 64, 3), 55, dtype=np.uint8)
    img[20:70, 10:54] = shirt_bgr
    img[70:120, 14:50] = pants_bgr
    cv2.line(img, (10, 40), (54, 40), (245, 245, 245), 2)
    return img


def test_strict_two_student_mode_rejects_bystander_and_avoids_duplicates() -> None:
    cfg = ReIDConfig(
        strict_target_mode=True,
        allow_unknown_fallback=False,
        combined_match_threshold=0.58,
        match_margin_threshold=0.05,
        enrolled_students={
            "001": EnrolledStudentProfileConfig(display_name="Student 1", custom_class_label="student_1"),
            "002": EnrolledStudentProfileConfig(display_name="Student 2", custom_class_label="student_2"),
        },
    )
    matcher = CrossingReIDMatcher(cfg, custom_class_map={"student_1": "001", "student_2": "002"})

    s1_crop = _solid_person_crop((230, 50, 20), (30, 40, 200))
    s2_crop = _solid_person_crop((210, 30, 180), (190, 180, 20))
    bystander_crop = _solid_person_crop((20, 220, 40), (20, 220, 220))

    matcher.register_student_reference("001", "Student 1", s1_crop, "student_1")
    matcher.register_student_reference("002", "Student 2", s2_crop, "student_2")

    t_s1 = TrackedObject("cam_1", 11, np.array([10, 10, 74, 138], np.float32), (42.0, 138.0), 0.9, "person", s1_crop, 200.0)
    t_bystander = TrackedObject("cam_1", 12, np.array([100, 10, 164, 138], np.float32), (132.0, 138.0), 0.9, "person", bystander_crop, 200.0)

    res_s1 = matcher.match_crossing("cam_1", t_s1, "INWARD", 10.0)
    assert res_s1.matched is True
    assert res_s1.person_id == "001"

    res_bystander = matcher.match_crossing("cam_1", t_bystander, "INWARD", 11.0)
    assert res_bystander.matched is False
    assert res_bystander.ignored_bystander is True

    # Cross-camera handoff to cam_2 matches same Student 1
    t_s1_cam2 = TrackedObject("cam_2", 5, np.array([20, 20, 84, 148], np.float32), (52.0, 148.0), 0.88, "person", s1_crop, 210.0)
    res_cam2 = matcher.match_crossing("cam_2", t_s1_cam2, "INWARD", 15.0)
    assert res_cam2.matched is True
    assert res_cam2.person_id == "001"

    # 1-to-1 Hungarian guard: even if two tracks resemble Student 1, at most ONE gets 001
    t_dup_s1 = TrackedObject("cam_1", 19, np.array([200, 10, 264, 138], np.float32), (232.0, 138.0), 0.8, "person", s1_crop, 200.0)
    assigned = matcher.match_frame_batch([t_s1, t_dup_s1, t_bystander])
    assigned_pids = [v.person_id for v in assigned.values()]
    assert assigned_pids.count("001") <= 1
    assert 12 not in assigned


def test_general_mode_unknown_track_fallback() -> None:
    cfg = ReIDConfig(strict_target_mode=False, appearance_match_threshold=0.90)
    matcher = CrossingReIDMatcher(cfg)
    unseen = TrackedObject(
        "cam_2",
        99,
        np.array([10, 10, 74, 138], np.float32),
        (42.0, 138.0),
        0.75,
        "person",
        _solid_person_crop((10, 10, 200), (200, 200, 200)),
        150.0,
    )
    res = matcher.match_crossing("cam_2", unseen, "INWARD", 25.0)
    assert res.matched is True
    assert res.person_id == "UNKNOWN_TRACK"
